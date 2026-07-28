from __future__ import annotations

import json
import os
import subprocess
import sys

from dano.catalog.manifest import to_manifest
from dano.export.agent_skills import (
    _capability_reference_md,
    _export_contract_errors,
    _options_md,
    _skill_md,
    _write_skill,
)
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
                        "pageNo": {
                            "type": "integer", "default": 1,
                            "x-dano-apply-default": True,
                        },
                        "pageSize": {
                            "type": "integer", "default": 10,
                            "x-dano-apply-default": True,
                        },
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


def _withdraw_relation_manifest():
    return to_manifest(SkillSpec(
        skill_id="A-OA.seal_apply",
        subsystem=Subsystem.OA,
        action="seal_apply",
        title="公章使用申请",
        risk_level=RiskLevel.L3,
        capabilities=[
            {
                "name": "query_seal_apply",
                "kind": "query_status",
                "title": "查询公章使用申请",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pageNo": {
                            "type": "integer",
                            "default": 1,
                            "x-dano-apply-default": True,
                        },
                        "pageSize": {
                            "type": "integer",
                            "default": 10,
                            "x-dano-apply-default": True,
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
                                "properties": {
                                    "billCode": {
                                        "type": "string",
                                        "title": "单据编号",
                                        "x-dano-identifier-role": "business_document",
                                    },
                                    "processInstanceId": {
                                        "type": "string",
                                        "x-dano-identifier-role": "process_instance",
                                        "x-dano-display": False,
                                    },
                                },
                            },
                        },
                        "total": {"type": "integer"},
                    },
                },
            },
            {
                "name": "withdraw_seal_apply",
                "kind": "withdraw",
                "title": "撤回公章使用申请",
                "requires_human_confirm": True,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "流程实例ID": {
                            "type": "string",
                            "x-flow-path": "id",
                            "x-dano-identifier-role": "process_instance",
                            "x-dano-derived-from-query": True,
                            "x-dano-source-capability": "query_seal_apply",
                            "x-dano-source-output": "records[].processInstanceId",
                            "x-dano-require-current-value": True,
                        },
                        "撤回原因": {
                            "type": "string",
                            "default": "填写有误",
                        },
                    },
                    "required": ["流程实例ID", "撤回原因"],
                },
                "output_schema": {"type": "object"},
            },
        ],
        capability_relations=[{
            "relation_id": "rel_query_withdraw",
            "type": "external_transform",
            "from_capability": "query_seal_apply",
            "from_output": "records[].processInstanceId",
            "to_capability": "withdraw_seal_apply",
            "to_input": "流程实例ID",
            "caller_responsibility": "从用户所选记录复制流程实例ID",
        }],
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
    assert "多个表单" in markdown
    assert "一次性汇总" in markdown
    assert "不得按表单" in markdown
    assert "每次回复" in markdown and "一次" in markdown
    assert "只收集一个非确认字段" in markdown
    assert "业务上确实必填" in markdown
    assert "多题按 `questions[].id`" in markdown
    assert "非空推荐 `default`" in markdown
    assert "录制样例必须保留为推荐值" in markdown
    assert "推荐默认值只用于 `ask_user_question` 展示" in markdown
    assert "`x-dano-apply-default: true`" in markdown
    assert "取消" in markdown and "停止" in markdown
    assert "校验失败" in markdown and "静默" in markdown
    assert "顶层 `title` 与 `questions[]`" in markdown
    assert "`formIds[]` 与 `confirm: true`" in markdown
    assert "`question` 与 `confirm: true`" not in markdown
    assert "`status=answered`" in markdown
    assert "HTTP 5xx、超时或结果不明" in markdown
    assert "禁止用 curl、直连目标接口" in markdown
    assert "禁止使用 curl、Python HTTP 客户端" in markdown
    assert "`partial_success`" not in markdown

    interaction = manifest.call_protocol["interaction_protocol"]
    assert interaction["max_calls_per_assistant_response"] == 1
    assert interaction["non_confirmation_default"]["string_must_be_non_empty"] is True
    assert interaction["non_confirmation_default"]["recorded_schema_default_must_be_copied_exactly"] is True
    assert interaction["parameter_identity"]["question_id_must_equal_input_key"] is True
    assert interaction["query_input_policy"] == {
        "explicit_business_filters_only": True,
        "recorded_filter_defaults_forbidden": True,
        "safe_defaults_require": "x-dano-apply-default=true",
        "empty_filters_use_empty_input": True,
        "nearest_capability_substitution_forbidden": True,
    }
    assert interaction["confirmation"]["allowed_keys"] == ["formIds", "confirm"]
    assert interaction["confirmation"]["same_assistant_turn"] is True
    assert interaction["result_statuses"] == ["answered", "confirmed", "cancelled"]
    assert interaction["single_field_collection"]["mode"] == "top_level"
    assert interaction["multi_field_collection"]["top_level_field_configuration_forbidden"] is True
    assert interaction["multi_field_collection"]["top_level_required"] == ["title", "questions"]
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
        "invent_or_replace_forbidden": True,
        "default_must_match_candidate": True,
        "first_candidate_fallback_forbidden": True,
    }
    assert interaction["answer_mapping"]["multiple"].startswith("result.answer object")
    assert interaction["validation_error_behavior"].startswith("retry_silently")
    assert all("interaction_protocol" in cap["call_protocol"] for cap in manifest.capabilities)
    assert _export_contract_errors(manifest) == []


def test_exported_skill_summarizes_successful_writes_in_business_language():
    markdown = _skill_md(_hotel_manifest(), "dano-a-oa-hotel-apply")

    assert "成功的写操作使用能力标题给出业务化完成结论" in markdown
    assert "`result.code`、`result.data`、`result.msg`" in markdown
    assert "不得逐项展示" in markdown
    assert "裸 `true`、内部 ID 或空消息" in markdown


def test_exported_skill_validates_field_content_before_confirmation():
    markdown = _skill_md(_hotel_manifest(), "dano-a-oa-hotel-apply")

    assert "## 字段格式与内容校验" in markdown
    assert "确认前、执行前" in markdown
    assert "金额、数量、人数" in markdown
    assert "传输类型仍是字符串" in markdown
    assert "不得进入确认或执行" in markdown
    assert "不得臆造最小值、最大值" in markdown


def test_exported_skill_renders_schema_defaults_in_tables_and_examples():
    reference = _capability_reference_md(_hotel_manifest())
    query_fields = reference.split(
        "## 查询酒店申请记录", 1,
    )[1].split("## 撤回酒店申请", 1)[0]
    write_fields = reference.split("## 撤回酒店申请", 1)[1]

    assert "推荐默认值" in reference
    assert "录制推荐值，需用户确认" in reference
    assert "| `pageNo` | pageNo | `text` | integer | 否 | `1`（安全默认值）" in query_fields
    assert "| `pageSize` | pageSize | `text` | integer | 否 | `10`（安全默认值）" in query_fields
    assert '"审批中"`（录制参考值，禁止自动作为查询条件）' in query_fields
    assert '"行程变更"`（录制推荐值，需用户确认）' in write_fields


def test_exported_skill_preserves_the_published_capability_contract(tmp_path):
    manifest = _hotel_manifest()
    folder = _write_skill(tmp_path, manifest)
    exported = json.loads(
        (folder / "references" / "CONTRACT.json").read_text(encoding="utf-8")
    )
    withdraw = next(
        capability for capability in exported["capabilities"]
        if capability["name"] == "withdraw_hotel_apply"
    )
    resource_id = withdraw["input_schema"]["properties"]["id"]
    protocol_resource_id = withdraw["call_protocol"]["input_schema"]["properties"]["id"]

    assert resource_id == {
        "type": "string",
        "default": "OA-JDSQ-20260713001",
    }
    assert protocol_resource_id == resource_id
    assert "request_refs" not in withdraw
    assert "nodes" not in withdraw
    assert all(
        "input_schema" not in capability
        for capability in exported.get("call_metadata", {}).get("capabilities", [])
    )


def test_multi_capability_export_uses_an_explicit_invocation_envelope(tmp_path):
    manifest = _hotel_manifest()
    folder = _write_skill(tmp_path, manifest)
    exported = json.loads(
        (folder / "references" / "CONTRACT.json").read_text(encoding="utf-8")
    )

    assert exported["capability"] is None
    assert exported["call_protocol"]["default_capability"] is None
    assert exported["call_protocol"]["requires_explicit_capability"] is True
    assert exported["call_protocol"]["payload"]["capability"] is None
    assert exported["parameters"]["required"] == ["capability", "input"]
    assert exported["parameters"]["properties"]["capability"]["enum"] == [
        "query_hotel_apply", "withdraw_hotel_apply",
    ]
    assert exported["parameters"]["x-dano-capability-input-schemas"] == {
        "query_hotel_apply": "#/capabilities/0/input_schema",
        "withdraw_hotel_apply": "#/capabilities/1/input_schema",
    }
    assert exported["call_protocol"]["input_schema"] == exported["parameters"]
    assert exported["output_schema"]["required"] == ["status"]


def test_multi_capability_skill_does_not_invent_missing_relations():
    markdown = _skill_md(_hotel_manifest(), "dano-a-oa-hotel-apply")

    assert "当前发布契约未声明 `capability_relations`" in markdown
    assert "按**独立能力**处理" in markdown
    assert "不得自行编造自动串联、字段映射或执行顺序" in markdown


def test_related_withdraw_supports_single_and_caller_orchestrated_batch(tmp_path):
    manifest = _withdraw_relation_manifest()
    markdown = _skill_md(manifest, "dano-a-oa-seal-apply")

    assert "## 单条与批量撤回编排" in markdown
    assert "撤回这个提交" in markdown
    assert "本会话最后一次成功写操作" in markdown
    assert "匹配为零或多条时" in markdown
    assert "records[].processInstanceId" in markdown
    assert "撤回全部" in markdown
    assert "遍历所有分页" in markdown
    assert "不能只处理当前页或默认前 10 条" in markdown
    assert "`checkbox`" in markdown
    assert "逐条调用`withdraw_seal_apply`" in markdown
    assert "不得并发、不得自动重试" in markdown
    assert "`partial_success`" in markdown

    folder = _write_skill(tmp_path, manifest)
    exported = json.loads(
        (folder / "references" / "CONTRACT.json").read_text(encoding="utf-8")
    )
    policy = exported["relation_orchestration"]
    rule = policy["rules"][0]
    assert policy["mode"] == "caller_orchestrated"
    assert rule["source_capability"] == "query_seal_apply"
    assert rule["source_output"] == "records[].processInstanceId"
    assert rule["target_capability"] == "withdraw_seal_apply"
    assert rule["target_input"] == "流程实例ID"
    assert rule["single_reference"]["require_unique_record"] is True
    assert rule["plural_reference"] == {
        "requires_explicit_plural_intent": True,
        "query_every_page": True,
        "selection_scope": "all_records_matching_explicit_user_scope",
        "confirmation": "one_combined_form_for_all_selected_records",
        "execution": "sequential_single_capability_invocations",
        "automatic_retry": False,
        "result": "per_record_with_partial_success",
    }
    withdraw = next(
        capability
        for capability in exported["capabilities"]
        if capability["name"] == "withdraw_seal_apply"
    )
    assert (
        withdraw["call_protocol"]["relation_orchestration"]
        == exported["call_protocol"]["relation_orchestration"]
        == policy
    )


def test_exported_skill_does_not_rewrite_capability_field_types():
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.hotel_types",
        subsystem=Subsystem.OA,
        action="hotel_types",
        title="酒店申请",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_hotel_apply",
            "kind": "submit",
            "requires_human_confirm": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "预计金额": {
                        "type": "string", "default": "500",
                        "x-dano-business-type": "text", "x-dano-wire-type": "string",
                    },
                    "房间数量": {
                        "type": "string", "default": "2",
                        "x-dano-business-type": "text", "x-dano-wire-type": "string",
                    },
                    "使用城市": {
                        "type": "number", "default": 1,
                        "x-dano-business-type": "number", "x-dano-wire-type": "number",
                    },
                    "事项描述": {
                        "type": "string", "default": "出差",
                        "x-dano-business-type": "text",
                    },
                    "备注": {
                        "type": "string", "default": "靠近地铁",
                        "x-dano-business-type": "text",
                    },
                },
                "required": ["预计金额", "房间数量", "使用城市", "事项描述", "备注"],
            },
        }],
    ))

    markdown = _capability_reference_md(manifest)

    assert "| `预计金额` | 预计金额 | `text` | string |" in markdown
    assert "| `房间数量` | 房间数量 | `text` | string |" in markdown
    assert "| `使用城市` | 使用城市 | `text` | number |" in markdown
    assert "| `事项描述` | 事项描述 | `text` | string |" in markdown
    assert "| `备注` | 备注 | `text` | string |" in markdown


def test_exported_skill_forbids_query_filter_defaults_and_nearest_capability_guessing():
    manifest = _hotel_manifest()
    markdown = _skill_md(manifest, "dano-a-oa-hotel-apply")
    reference = _capability_reference_md(manifest)

    assert "查询 input 只能包含用户本轮明确指定的业务筛选条件" in markdown
    assert "录制推荐值不得作为查询筛选条件自动提交" in markdown
    assert "没有筛选条件时传空 input" in markdown
    assert "查询能力不得为可选筛选字段主动提问" in markdown
    assert "录制参考值，禁止自动作为查询条件" in reference
    assert "实体目录/候选列表" in markdown
    assert "不得用最相近的能力代替" in markdown


def test_exported_skill_locks_question_ids_defaults_and_enum_candidates_to_schema():
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.hotel_defaults",
        subsystem=Subsystem.OA,
        action="hotel_defaults",
        title="酒店申请",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_hotel_apply",
            "kind": "submit",
            "title": "提交酒店申请",
            "requires_human_confirm": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "房间类型": {
                        "type": "string",
                        "format": "name-ref",
                        "default": "标准间",
                        "x-enum-options": [
                            {"label": "标准间", "value": "1"},
                            {"label": "大床房", "value": "2"},
                        ],
                    },
                    "房间等级": {
                        "type": "string",
                        "format": "name-ref",
                        "default": "豪华",
                        "enum": ["普通", "舒适", "豪华", "行政"],
                    },
                },
                "required": ["房间类型", "房间等级"],
            },
        }],
    ))

    markdown = _skill_md(manifest, "dano-a-oa-hotel-defaults")
    reference = _capability_reference_md(manifest)

    assert "参数名逐字一致" in markdown
    assert "禁止翻译、改名或改成 snake_case" in markdown
    assert "所选能力参考小节是唯一表单来源" in markdown
    assert "“推荐默认值”列的主值逐字复制为表单 `default`" in markdown
    assert "括号内录制值只用于溯源" in markdown
    assert "禁止自行生成、替换、增删候选项" in markdown
    assert "枚举默认值必须等于候选的稳定 `id`" in markdown
    assert "禁止回落为候选第一项" in markdown
    assert '`options: [{"id": "1", "label": "标准间"}, {"id": "2", "label": "大床房"}]`' in reference
    assert '`"1"`（录制推荐值，需用户确认；录制值 `"标准间"`；能力值 `"标准间"`）' in reference
    assert '`options: [{"id": "普通", "label": "普通"}, {"id": "舒适", "label": "舒适"}, {"id": "豪华", "label": "豪华"}, {"id": "行政", "label": "行政"}]`' in reference
    assert '`"豪华"`（录制推荐值，需用户确认）' in reference


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
                            "value_key": "id", "label_key": "name",
                            "category_key": "type", "category_value": "hotel_city",
                            "result_path": "data.records"}},
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
                            "category_key": "type", "category_value": "hotel_city",
                            "result_path": "data.records",
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
    capability_reference = (
        folder / "references" / "CAPABILITIES.md"
    ).read_text(encoding="utf-8")

    assert 'name: "酒店申请"' in markdown
    assert "3. **一次性收集全部表单项。**" in markdown
    assert "`submit_hotel_apply`" in markdown
    assert "| `hotelName` | 酒店名称 | `text`" in capability_reference
    assert "| `city` | 城市 | `select`" in capability_reference
    assert '"endpoint": "/api/cities"' in capability_reference
    assert '"params": {"type": "hotel_city"}' in capability_reference
    assert '"resultPath": "data.records"' in capability_reference
    assert "| `remark` | 申请说明 | `textarea`" in capability_reference
    assert "### 固定表单请求" in capability_reference
    assert '"title": "提交酒店申请"' in capability_reference
    assert '"id": "hotelName"' in capability_reference
    assert '"question": "酒店名称"' in capability_reference
    assert '"inputType": "textarea"' in capability_reference
    assert "不得展示原始 JSON" in capability_reference
    assert "按 `answer` 对象的 `id` 映射为能力参数" in markdown
    assert "Markdown 表格呈现" in markdown
    assert "target=\"_blank\"" in markdown
    assert "\n\n| `need_select`" not in markdown
    assert (folder / "scripts" / "format_list.ps1").is_file()


def test_exported_skill_is_compact_and_routes_details_to_references(tmp_path):
    folder = _write_skill(tmp_path, _hotel_manifest())
    markdown = (folder / "SKILL.md").read_text(encoding="utf-8")

    assert "完整 output schema" not in markdown
    assert "完整机器契约见 `references/CONTRACT.json`" in markdown
    assert "字段和表单控件见 `references/CAPABILITIES.md`" in markdown
    assert (folder / "references" / "CAPABILITIES.md").is_file()
    assert "## 何时使用" not in markdown
    assert "\n## 示例\n" not in markdown
    assert markdown.count("## 错误处理") == 1
    assert "## 故障排除" not in markdown
    assert "事实核查未过" not in markdown
    assert markdown.index("## 运行前置") < markdown.index("## 操作步骤(SOP)")
    assert len(markdown.splitlines()) < 260

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


def test_exported_list_formatter_is_driven_by_output_schema_metadata(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="A-ERP.ticket_inspection",
        subsystem=Subsystem("A-ERP"),
        action="ticket_inspection",
        title="工单检查",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "inspect_ticket",
            "kind": "inspect",
            "title": "检查工单",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "output_schema": {
                "type": "object",
                "properties": {
                    "records": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "privatePointer": {
                                "type": "string",
                                "x-dano-display": False,
                                "x-dano-identifier-role": "record",
                            },
                            "workflowToken": {
                                "type": "string",
                                "x-dano-display": False,
                                "x-dano-identifier-role": "process_instance",
                            },
                            "ticketNumber": {
                                "type": "string",
                                "title": "工单编号",
                                "x-dano-display-order": 10,
                                "x-dano-identifier-role": "business_document",
                            },
                            "lifecycleCode": {
                                "type": "string",
                                "title": "处理状态",
                                "x-dano-display-order": 20,
                                "x-enum-options": [
                                    {"value": "P", "label": "处理中"},
                                    {"value": "D", "label": "已完成"},
                                ],
                            },
                            "openedAtValue": {
                                "type": "number",
                                "title": "创建时间",
                                "x-dano-display-order": 30,
                                "x-dano-value-format": "epoch-milliseconds",
                            },
                            "memoValue": {
                                "type": "string",
                                "title": "说明",
                                "x-dano-display-order": 40,
                            },
                            "unclassifiedCode": {"type": "string"},
                        }},
                    },
                    "total": {"type": "number"},
                },
            },
        }],
    ))
    folder = _write_skill(tmp_path, manifest)
    formatter = folder / "scripts" / "format_list.py"
    result = subprocess.run(
        [sys.executable, str(formatter), "--json", json.dumps({
            "capability": "inspect_ticket",
            "output": {"records": [{
                "privatePointer": "internal-42",
                "workflowToken": "workflow-42",
                "memoValue": "跨系统验证",
                "unclassifiedCode": "raw-42",
                "openedAtValue": 1784955106000,
                "lifecycleCode": "P",
                "ticketNumber": "ERP-42",
            }]},
        }, ensure_ascii=False)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "DANO_DISPLAY_TIMEZONE": "Asia/Shanghai"},
    )

    assert "| 工单编号 | 处理状态 | 创建时间 | 说明 | unclassifiedCode |" in result.stdout
    assert "| ERP-42 | 处理中 | 2026-07-25 12:51 | 跨系统验证 | raw-42 |" in result.stdout
    assert "internal-42" not in result.stdout
    assert "workflow-42" not in result.stdout
    markdown = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert "`x-dano-identifier-role`" in markdown
    assert "禁止根据字段名、值形状或当前业务场景猜测" in markdown


def test_exported_list_formatter_reuses_grounded_metadata_within_capability(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="custom-system.request",
        subsystem=Subsystem("custom-system"),
        action="request",
        title="业务申请",
        risk_level=RiskLevel.L3,
        capabilities=[
            {
                "name": "query_request",
                "kind": "query_status",
                "title": "查询申请",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "状态筛选": {
                            "type": "string",
                            "label": "处理状态",
                            "x-flow-path": "query.phaseCode",
                            "x-options-snapshot": [
                                {"value": "P", "label": "处理中"},
                                {"value": "D", "label": "已完成"},
                            ],
                        },
                        "创建时间": {
                            "type": "string",
                            "label": "创建时间",
                            "x-flow-path": "query.openedValue",
                            "x-dano-business-type": "datetime",
                        },
                    },
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "records": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "phaseCode": {"type": "string"},
                                    "openedValue": {"type": "number"},
                                },
                            },
                        },
                    },
                },
            },
        ],
    ))
    folder = _write_skill(tmp_path, manifest)
    result = subprocess.run(
        [
            sys.executable,
            str(folder / "scripts" / "format_list.py"),
            "--capability",
            "query_request",
            "--json",
            json.dumps({
                "records": [{"phaseCode": "P", "openedValue": 1784955106000}],
            }),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "DANO_DISPLAY_TIMEZONE": "Asia/Shanghai"},
    )

    assert "| 处理状态 | 创建时间 |" in result.stdout
    assert "| 处理中 | 2026-07-25 12:51 |" in result.stdout


def test_exported_list_formatter_does_not_alias_same_wire_name_across_capabilities(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="custom-system.request",
        subsystem=Subsystem("custom-system"),
        action="request",
        title="业务申请",
        risk_level=RiskLevel.L3,
        capabilities=[
            {
                "name": "query_request",
                "kind": "query_status",
                "title": "查询申请",
                "input_schema": {"type": "object", "properties": {}},
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
                "name": "withdraw_request",
                "kind": "withdraw",
                "title": "撤回申请",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "流程实例ID": {
                            "type": "string",
                            "label": "流程实例ID",
                            "x-flow-path": "id",
                        },
                    },
                },
                "output_schema": {"type": "object", "properties": {}},
            },
        ],
    ))
    folder = _write_skill(tmp_path, manifest)
    result = subprocess.run(
        [
            sys.executable,
            str(folder / "scripts" / "format_list.py"),
            "--capability",
            "query_request",
            "--json",
            json.dumps({"records": [{"id": "record-42"}]}),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "| id |" in result.stdout
    assert "流程实例ID" not in result.stdout


def test_exported_description_is_a_compact_trigger_index():
    markdown = _skill_md(_hotel_manifest(), "dano-a-oa-hotel-apply")
    frontmatter = markdown.split("---", 2)[1]

    assert "用于“酒店申请”业务" in frontmatter
    assert "用户明确要求执行“查询酒店申请记录、撤回酒店申请”中的任一已发布操作时使用" in frontmatter
    assert "负责选择正确能力、一次性收集表单参数、确认写操作并返回执行结果" in frontmatter
    assert "仅咨询、业务对象不一致或要求未列出的操作时不要触发" in frontmatter
    assert "仅用于这些已发布能力" not in frontmatter


def test_dynamic_options_keep_stable_ids_and_duplicate_labels_for_question_tool(tmp_path):
    folder = _write_skill(tmp_path, _hotel_manifest())
    script = folder / "scripts" / "dano_call.py"
    namespace: dict = {"__name__": "generated_skill_audit"}
    exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), namespace)

    assert namespace["_question_options"]([
        {"label": "公司章", "value": "internal-1"},
        {"label": "财务章", "value": "internal-2"},
        {"label": "公司章", "value": "duplicate"},
    ]) == [
        {"id": "internal-1", "label": "公司章"},
        {"id": "internal-2", "label": "财务章"},
        {"id": "duplicate", "label": "公司章"},
    ]


def test_export_rejects_ambiguous_or_unknown_static_option_defaults():
    def errors_for(default: str) -> list[str]:
        return _export_contract_errors(to_manifest(SkillSpec(
            skill_id=f"A-OA.option_default_{default}",
            subsystem=Subsystem.OA,
            action="option_default",
            title="选项默认值",
            risk_level=RiskLevel.L2,
            capabilities=[{
                "name": "submit_option",
                "kind": "submit",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "assignee": {
                            "type": "string",
                            "format": "name-ref",
                            "default": default,
                            "x-enum-options": [
                                {"value": "user-1", "label": "张三"},
                                {"value": "user-2", "label": "张三"},
                            ],
                        },
                    },
                    "required": ["assignee"],
                },
            }],
        )))

    assert any("多个同名候选" in error for error in errors_for("张三"))
    assert any("不在静态候选" in error for error in errors_for("李四"))
    assert errors_for("user-2") == []


def test_datetime_recommendations_match_question_control_format():
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.datetime_defaults",
        subsystem=Subsystem.OA,
        action="datetime_defaults",
        title="日期申请",
        risk_level=RiskLevel.L2,
        capabilities=[{
            "name": "submit_datetime",
            "kind": "submit",
            "title": "提交日期申请",
            "requires_human_confirm": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "开始时间": {
                        "type": "string", "format": "date-time",
                        "default": "2026-07-01 16:00:00",
                    },
                    "精确时间": {
                        "type": "string", "format": "date-time",
                        "default": "2026-07-01 16:00:30",
                    },
                },
                "required": ["开始时间", "精确时间"],
            },
        }],
    ))

    reference = _capability_reference_md(manifest)
    assert "| `开始时间` | 开始时间 | `date` / `yyyy-MM-dd HH:mm`" in reference
    assert '`"2026-07-01 16:00"`（录制推荐值，需用户确认；录制值 `"2026-07-01 16:00:00"`）' in reference
    assert "| `精确时间` | 精确时间 | `text` | datetime" in reference
    assert '`"2026-07-01 16:00:30"`（录制推荐值，需用户确认）' in reference
    assert '"inputType": "date"' in reference
    assert '"dateFormat": "yyyy-MM-dd HH:mm"' in reference
    assert '"default": "2026-07-01 16:00"' in reference


def test_generated_runtime_converts_form_values_without_changing_contract(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.form_projection",
        subsystem=Subsystem.OA,
        action="form_projection",
        title="表单投影",
        risk_level=RiskLevel.L2,
        capabilities=[{
            "name": "submit_form",
            "kind": "submit",
            "input_schema": {
                "type": "object",
                "properties": {
                    "roomType": {
                        "type": "string",
                        "format": "name-ref",
                        "default": "标准间",
                        "x-enum-options": [
                            {"value": "room-1", "label": "标准间"},
                            {"value": "room-2", "label": "大床房"},
                        ],
                    },
                    "startAt": {
                        "type": "string",
                        "format": "date-time",
                        "default": "2026-07-01 16:00:00",
                    },
                    "enabled": {"type": "boolean", "default": True},
                    "entries": {
                        "type": "array",
                        "default": [{"date": "2026-07-01"}],
                        "items": {"type": "object"},
                    },
                    "pageNo": {
                        "type": "integer",
                        "default": 1,
                        "x-dano-apply-default": True,
                    },
                },
                "required": ["roomType", "startAt", "enabled", "entries"],
            },
        }],
    ))
    folder = _write_skill(tmp_path, manifest)
    script = folder / "scripts" / "dano_call.py"
    namespace: dict = {"__name__": "generated_projection_audit"}
    exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), namespace)
    contract = namespace["CAPABILITIES"]["submit_form"]
    arguments = namespace["_coerce_cli_values"]({
        "roomType": "room-1",
        "startAt": "2026-07-01 16:00",
        "enabled": "false",
        "entries": '[{"date":"2026-07-01"}]',
    }, contract)

    assert arguments == {
        "roomType": "标准间",
        "startAt": "2026-07-01 16:00:00",
        "enabled": False,
        "entries": [{"date": "2026-07-01"}],
    }
    assert namespace["_apply_safe_defaults"](arguments, contract)["pageNo"] == 1
    assert contract["parameters"] == manifest.capabilities[0]["input_schema"]
    reference = (folder / "references" / "CAPABILITIES.md").read_text(encoding="utf-8")
    assert '"inputType": "select"' in reference
    assert '"id": "room-1"' in reference
    assert '"label": "标准间"' in reference
    assert '"default": "room-1"' in reference
    assert '"default": "[{\\"date\\":\\"2026-07-01\\"}]"' in reference


def test_windows_wrapper_preserves_json_and_formatter_accepts_bom(tmp_path):
    folder = _write_skill(tmp_path, _hotel_manifest())
    formatter = folder / "scripts" / "format_list.py"
    formatted = subprocess.run(
        [sys.executable, str(formatter)],
        input="\ufeff" + json.dumps({"records": [{"id": "H1"}]}),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "| H1 |" in formatted.stdout

    if os.name != "nt":
        return
    payload = json.dumps(
        {"id": "CURRENT-1", "撤回原因": "行程变更"},
        ensure_ascii=False,
    )
    encoded = __import__("base64").b64encode(payload.encode()).decode()
    command = (
        f"$payload=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'));"
        f"& '{folder / 'scripts' / 'submit.ps1'}' --capability withdraw_hotel_apply --json $payload"
    )
    env = dict(os.environ, DANO_URL="http://127.0.0.1:1", DANO_TENANT_KEY="audit-only")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert json.loads(result.stdout)["status"] == "need_confirm"

    formatted = subprocess.run(
        [
            "powershell", "-NoProfile", "-File",
            str(folder / "scripts" / "format_list.ps1"),
            json.dumps({"records": [{"名称": "杭州酒店"}]}, ensure_ascii=False),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "| 名称 |" in formatted.stdout
    assert "| 杭州酒店 |" in formatted.stdout
