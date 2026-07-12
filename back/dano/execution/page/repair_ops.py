"""LLM 修复循环 P0:确定性**执行器** + **检出器**。

设计铁律:LLM 只输出"受限词表"里的修复操作(remap/parameterize/link/drop/reorder/rename/...),
**由本模块确定性执行**,引用必须指向真实存在的 param/path/step,否则该操作被拒(不执行);执行后调用方
重跑 self_check 复验 —— **结构永远错不了**(LLM 改不坏)。检出器给确定性 findings(会话专属常量焊死、占位名)。
"""
from __future__ import annotations

import copy
import re
from typing import Any

from dano.execution.page.request_capture import (
    _PATH_MISSING, _leaf_paths, _path_lookup, _set_by_path, _split_path, _tokens_to_str, self_check,
)

_SESSION_ID_RE = re.compile(r"^[A-Za-z]{2,}[-_]\d{4,}")        # SEQ-20260625-2F29 等"前缀+长数字段"码
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-")
_PLACEHOLDER_NAME_RE = re.compile(r"^(请输入|请选择|请填写|如\s|例如|placeholder)")

_FIX_OPS = {"drop_step", "reorder_steps", "set_success_rule", "parameterize",
            "link_step", "rename_param", "remap_field", "set_identity", "bind_placeholder"}
_CAPABILITY_KINDS = {"query_status", "list_options", "validate_batch", "submit_batch", "submit"}
_BATCH_KINDS = {"validate_batch", "submit_batch"}


def _schema_properties(schema: Any) -> dict:
    return (schema.get("properties") or {}) if isinstance(schema, dict) else {}


def _field_type(capability: dict, name: str, *, output: bool) -> str:
    schema = capability.get("output_schema" if output else "input_schema") or {}
    prop = _schema_properties(schema).get(name) or {}
    if isinstance(prop, dict) and prop.get("type"):
        return str(prop["type"])
    scopes = ("outputs",) if output else ("inputs", "fields")
    for scope in scopes:
        for field in capability.get(scope) or []:
            if not isinstance(field, dict):
                continue
            if name in {field.get("key"), field.get("path"), field.get("display_name")}:
                return str(field.get("type") or "")
    return ""


def _capability_refs(capability: dict) -> set[str]:
    return {str(v) for v in (capability.get("name"), capability.get("capability_id")) if v}


def _capability_fields(capability: dict, *, output: bool) -> list[dict]:
    fields = [field for field in (capability.get("outputs" if output else "inputs") or [])
              if isinstance(field, dict)]
    seen = {
        (str(field.get("field_id") or ""), str(field.get("scope") or ""),
         str(field.get("step_id") or ""), str(field.get("path") or field.get("key") or ""))
        for field in fields
    }
    for field in capability.get("fields") or []:
        if not isinstance(field, dict):
            continue
        scope = str(field.get("scope") or "input")
        marker = (
            str(field.get("field_id") or ""), scope, str(field.get("step_id") or ""),
            str(field.get("path") or field.get("key") or ""),
        )
        if ((output and scope == "output") or (not output and scope in {"input", "request_field"})) and marker not in seen:
            fields.append(field)
            seen.add(marker)
    return fields


def collect_capability_findings(api_request: dict) -> list[dict]:
    """Validate the exported capability contract without importing ``flow_spec``.

    The publishing path only has the compiled ``api_request``.  Keeping this
    validator data-oriented lets onboarding re-check the exact artifact it will
    persist, including batch, relation and Goal contracts.
    """
    capabilities = [c for c in (api_request.get("capabilities") or []) if isinstance(c, dict)]
    if not capabilities:
        return []
    out: list[dict] = []
    by_ref: dict[str, dict] = {}
    seen_names: set[str] = set()
    for index, cap in enumerate(capabilities):
        name = str(cap.get("name") or "")
        kind = str(cap.get("kind") or "")
        target = {"capability": name, "capability_index": index}
        if not name:
            out.append({"kind": "capability_name_missing", **target, "detail": "Capability 缺少 name"})
        elif name in seen_names:
            out.append({"kind": "capability_name_duplicate", **target, "detail": f"Capability `{name}` 重名"})
        seen_names.add(name)
        if kind not in _CAPABILITY_KINDS:
            out.append({"kind": "capability_kind_invalid", **target,
                        "detail": f"Capability `{name or index}` kind `{kind}` 不合法"})
        for ref in _capability_refs(cap):
            by_ref[ref] = cap

        contract = cap.get("execution_contract")
        if not isinstance(contract, dict):
            contract = {}
        step_ids = cap.get("compiled_step_ids") or cap.get("step_ids") or []
        calls = contract.get("call_order") or []
        if not step_ids and not calls:
            out.append({"kind": "capability_execution_missing", **target,
                        "detail": f"Capability `{name or index}` 没有可执行步骤"})

        input_schema = cap.get("input_schema") or {}
        props = _schema_properties(input_schema)
        for required in input_schema.get("required") or [] if isinstance(input_schema, dict) else []:
            if required not in props:
                out.append({"kind": "capability_required_field_missing", **target, "field": required,
                            "detail": f"Capability `{name or index}` required 字段 `{required}` 不在 input_schema.properties"})
        for field in _capability_fields(cap, output=False):
            if not isinstance(field, dict) or not field.get("required") or not field.get("exposed_to_caller", True):
                continue
            key = str(field.get("key") or field.get("path") or "")
            if key and key not in props:
                out.append({"kind": "capability_input_schema_missing", **target, "field": key,
                            "detail": f"Capability `{name or index}` 输入字段 `{key}` 未进入 input_schema"})

        output_mapping = cap.get("output_mapping") or contract.get("return") or []
        output_schema = cap.get("output_schema") or {}
        output_props = _schema_properties(output_schema)
        for required in output_schema.get("required") or [] if isinstance(output_schema, dict) else []:
            if required not in output_props:
                out.append({"kind": "capability_required_output_missing", **target, "field": required,
                            "detail": f"Capability `{name or index}` required 输出 `{required}` 不在 output_schema.properties"})
        for field in _capability_fields(cap, output=True):
            key = str(field.get("key") or field.get("path") or "")
            if key and key not in output_props:
                out.append({"kind": "capability_output_schema_missing", **target, "field": key,
                            "detail": f"Capability `{name or index}` 输出字段 `{key}` 未进入 output_schema"})
        if not output_mapping and not _schema_properties(output_schema):
            out.append({"kind": "capability_output_missing", **target,
                        "detail": f"Capability `{name or index}` 缺少可解释输出"})
        valid_steps = {str(s) for s in step_ids if s}
        valid_steps.update(str(c.get("step_id")) for c in calls if isinstance(c, dict) and c.get("step_id"))
        for mapping_index, mapping in enumerate(output_mapping):
            if not isinstance(mapping, dict):
                out.append({"kind": "capability_output_invalid", **target, "mapping_index": mapping_index,
                            "detail": f"Capability `{name or index}` 输出映射不是对象"})
                continue
            source_step = str(mapping.get("step_id") or "")
            if source_step and valid_steps and source_step not in valid_steps:
                out.append({"kind": "capability_output_step_missing", **target, "mapping_index": mapping_index,
                            "detail": f"Capability `{name or index}` 输出引用能力外步骤 `{source_step}`"})

        batch = contract.get("batch") or {}
        if kind in _BATCH_KINDS:
            entries = props.get(str(batch.get("items_field") or "entries")) or {}
            if not batch.get("enabled"):
                out.append({"kind": "capability_batch_disabled", **target,
                            "detail": f"批量 Capability `{name or index}` 的 execution_contract.batch 未启用"})
            if entries.get("type") != "array":
                out.append({"kind": "capability_batch_entries_missing", **target,
                            "detail": f"批量 Capability `{name or index}` 缺少 array 类型批量输入"})
            else:
                item_schema = entries.get("items") or {}
                if not item_schema.get("type") or (
                    item_schema.get("type") == "object" and not _schema_properties(item_schema)
                ):
                    out.append({"kind": "capability_batch_item_schema_missing", **target,
                                "detail": f"批量 Capability `{name or index}` 缺少可解释的条目字段 schema"})

    for index, relation in enumerate(api_request.get("capability_relations") or []):
        if not isinstance(relation, dict):
            out.append({"kind": "capability_relation_invalid", "relation_index": index,
                        "detail": "Capability relation 不是对象"})
            continue
        source = by_ref.get(str(relation.get("from_capability") or ""))
        target_cap = by_ref.get(str(relation.get("to_capability") or ""))
        base = {"relation_index": index, "relation_id": relation.get("relation_id")}
        if source is None or target_cap is None:
            out.append({"kind": "capability_relation_endpoint_missing", **base,
                        "detail": f"Capability relation `{relation.get('relation_id') or index}` 指向不存在的能力"})
            continue
        relation_kind = str(relation.get("mode") or relation.get("type") or "").strip().lower()
        has_field_mapping = bool(relation.get("from_output") and relation.get("to_input"))
        if relation_kind not in {"external_transform", "data_mapping", "field_mapping"} and not has_field_mapping:
            continue
        from_name, to_name = str(relation.get("from_output") or ""), str(relation.get("to_input") or "")
        from_type = _field_type(source, from_name, output=True)
        to_type = _field_type(target_cap, to_name, output=False)
        if not from_name or not to_name or not from_type or not to_type:
            out.append({"kind": "capability_relation_field_missing", **base,
                        "detail": f"Capability relation `{relation.get('relation_id') or index}` 字段不存在或类型未知"})
        elif from_type != to_type:
            out.append({"kind": "capability_relation_type_mismatch", **base,
                        "detail": f"Capability relation `{relation.get('relation_id') or index}` 类型不兼容: {from_type} -> {to_type}"})

    goal = api_request.get("goal")
    if isinstance(goal, dict):
        actual_names = {str(c.get("name")) for c in capabilities if c.get("name")}
        for name in goal.get("capabilities") or []:
            if str(name) not in actual_names:
                out.append({"kind": "goal_capability_missing", "capability": name,
                            "detail": f"Goal 引用了不存在的 Capability `{name}`"})
        available_inputs = {key for cap in capabilities for key in _schema_properties(cap.get("input_schema") or {})}
        available_outputs = {key for cap in capabilities for key in _schema_properties(cap.get("output_schema") or {})}
        for field in goal.get("required_inputs") or []:
            if str(field) not in available_inputs:
                out.append({"kind": "goal_required_input_missing", "field": field,
                            "detail": f"Goal 必填输入 `{field}` 未由任何 Capability 提供"})
        for field in goal.get("output_expectation") or []:
            raw_field = str(field or "").strip()
            explicit = raw_field.startswith(("field:", "output:"))
            field_name = raw_field.split(":", 1)[1].strip() if explicit else raw_field
            # output_expectation 也承载“返回审批结果”这类自然语言成功描述。
            # 只有显式字段引用或技术字段名才作为机器契约硬校验。
            technical_name = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\]-]*", field_name))
            if (explicit or technical_name) and field_name not in available_outputs:
                out.append({"kind": "goal_output_missing", "field": field_name,
                            "detail": f"Goal 期望输出 `{field_name}` 未由任何 Capability 提供"})
        forbidden = {str(x).lower() for x in (goal.get("forbidden_actions") or goal.get("forbidden_steps") or []) if x}
        for cap in capabilities:
            searchable = {str(cap.get("name") or "").lower(), str(cap.get("kind") or "").lower()}
            contract = cap.get("execution_contract") or {}
            searchable.update(str(call.get("path") or "").lower() for call in contract.get("call_order") or [] if isinstance(call, dict))
            for action in forbidden:
                if action and any(action == value or action in value for value in searchable):
                    out.append({"kind": "goal_forbidden_action", "capability": cap.get("name"), "action": action,
                                "detail": f"Capability `{cap.get('name')}` 命中 Goal 禁止动作 `{action}`"})
                    break
    return out


def looks_session_specific(value) -> bool:
    """像"一次性会话值"(任务ID/实例ID/时间戳/uuid/生成码)→ 绝不该当常量焊进 skill。
    稳健:只命中明显的一次性形态,放过 oa_leave 这类稳定业务常量。通用,不挑系统。"""
    s = str(value if value is not None else "").strip()
    if not s:
        return False
    if s.isdigit() and len(s) in (10, 13):                     # 10位秒 / 13位毫秒时间戳
        return True
    if _UUID_RE.match(s):
        return True
    if _SESSION_ID_RE.match(s) and re.search(r"\d{4,}", s):    # 前缀 + 含长数字段(日期/流水)
        return True
    return False


def looks_placeholder_name(name) -> bool:
    """像表单占位文字而非真字段名(请输入.../请选择.../如 X)。"""
    return bool(_PLACEHOLDER_NAME_RE.match(str(name or "").strip()))


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and v.startswith("{{") and v.endswith("}}")


def _find_param_tokens(template, param):
    """在 body_template 里找 {{param}} 占位的 tokens 路径;无则 None。"""
    needle = "{{" + str(param) + "}}"
    for _p, toks, sv, _raw in _leaf_paths(template):
        if sv == needle:
            return toks
    return None


def collect_repair_findings(api_request: dict) -> list[dict]:
    """**确定性** findings(给修复器线索,可单测):self_check 违规 + 会话专属常量焊死 + 占位名参数。
    LLM 审核的语义 findings(字段错配/业务逻辑)在 P1 合并进来。"""
    out: list[dict] = []
    for v in self_check(api_request):
        out.append({"kind": "self_check", "detail": v})
    for si, tgt in enumerate(api_request.get("steps") or [api_request]):
        templ = tgt.get("body_template")
        if isinstance(templ, (dict, list)):
            # 系统时间戳(submitTime/createTime)已标 system_values、运行期填 now → 不是"焊死会话值",免报(否则白拦发布)
            sys_paths = {s.get("path") for s in (tgt.get("system_values") or [])}
            sys_toks = {tuple(s.get("tokens") or []) for s in (tgt.get("system_values") or [])}
            link_paths = {lk.get("target_path") for lk in (tgt.get("links") or []) if lk.get("target_path")}
            link_toks = {tuple(_split_path(p)) for p in link_paths}
            for p, toks, sv, raw in _leaf_paths(templ):
                if p in sys_paths or tuple(toks) in sys_toks or p in link_paths or tuple(toks) in link_toks:
                    continue
                if not _is_placeholder(sv) and looks_session_specific(raw):
                    out.append({"kind": "session_constant", "step": si, "path": toks, "value": sv,
                                "detail": f"常量 `{p}`={sv} 像一次性会话值,不该焊进 skill(应串联/参数化/删步)"})
        for pm in (tgt.get("params") or []):
            if looks_placeholder_name(pm):
                out.append({"kind": "placeholder_name", "step": si, "param": pm,
                            "detail": f"参数名 `{pm}` 是占位文字,需改成真业务名"})
    out.extend(collect_capability_findings(api_request))
    return out


def apply_deterministic_repairs(api_request: dict) -> tuple[dict, list[dict]]:
    """Apply only repairs whose result follows uniquely from the compiled contract."""
    apir = copy.deepcopy(api_request)
    applied: list[dict] = []
    capabilities = [c for c in (apir.get("capabilities") or []) if isinstance(c, dict)]
    refs = {ref for cap in capabilities for ref in _capability_refs(cap)}
    used_names = {str(cap.get("name")) for cap in capabilities if cap.get("name")}
    for index, cap in enumerate(capabilities):
        kind = str(cap.get("kind") or "")
        if not cap.get("name") and kind in _CAPABILITY_KINDS and kind not in used_names:
            cap["name"] = kind
            used_names.add(kind)
            refs.add(kind)
            applied.append({"op": "derive_capability_name", "capability_index": index, "name": kind})
        if kind not in _CAPABILITY_KINDS and str(cap.get("name") or "") in _CAPABILITY_KINDS:
            kind = str(cap["name"])
            cap["kind"] = kind
            applied.append({"op": "derive_capability_kind", "capability": cap["name"], "kind": kind})
        name = cap.get("name") or index
        input_fields = _capability_fields(cap, output=False)
        schema = cap.get("input_schema")
        if not isinstance(schema, dict) and (input_fields or kind in _BATCH_KINDS):
            schema = {"type": "object", "properties": {}}
            cap["input_schema"] = schema
            applied.append({"op": "create_input_schema", "capability": name})
        if isinstance(schema, dict):
            props = schema.setdefault("properties", {})
            required = list(schema.get("required") or [])
            for field in input_fields:
                if not field.get("exposed_to_caller", True):
                    continue
                key = str(field.get("key") or field.get("path") or "")
                if key and key not in props:
                    props[key] = {"type": str(field.get("type") or "string")}
                    applied.append({"op": "add_input_schema_field", "capability": name, "field": key})
                if key and field.get("required") and key not in required:
                    required.append(key)
            clean_required = [key for key in required if key in props]
            if clean_required != required:
                applied.append({"op": "prune_schema_required", "capability": name})
            if schema.get("required") != clean_required:
                schema["required"] = clean_required
                applied.append({"op": "sync_schema_required", "capability": name})

        contract = cap.get("execution_contract")
        if not isinstance(contract, dict):
            contract = {}
            cap["execution_contract"] = contract
        if kind in _BATCH_KINDS:
            batch = contract.setdefault("batch", {})
            if not batch.get("enabled"):
                batch["enabled"] = True
                batch.setdefault("items_field", "entries")
                applied.append({"op": "enable_batch_contract", "capability": name})
            if isinstance(schema, dict):
                items_field = str(batch.get("items_field") or "entries")
                if _schema_properties(schema).get(items_field, {}).get("type") != "array":
                    item_properties = {}
                    item_required = []
                    for field in input_fields:
                        key = str(field.get("key") or field.get("path") or "")
                        if not key or key == items_field:
                            continue
                        item_properties[key] = {"type": str(field.get("type") or "string")}
                        if field.get("required"):
                            item_required.append(key)
                    item_schema = {"type": "object", "properties": item_properties}
                    if item_required:
                        item_schema["required"] = item_required
                    schema.setdefault("properties", {})[items_field] = {"type": "array", "items": item_schema}
                    required = list(schema.get("required") or [])
                    if items_field not in required:
                        schema["required"] = [*required, items_field]
                    applied.append({"op": "add_batch_input_schema", "capability": name,
                                    "field": items_field})

        output_fields = _capability_fields(cap, output=True)
        output_schema = cap.get("output_schema")
        if not isinstance(output_schema, dict) and output_fields:
            output_schema = {"type": "object", "properties": {}}
            cap["output_schema"] = output_schema
            applied.append({"op": "create_output_schema", "capability": name})
        if isinstance(output_schema, dict):
            output_props = output_schema.setdefault("properties", {})
            output_required = list(output_schema.get("required") or [])
            for field in output_fields:
                key = str(field.get("key") or field.get("path") or "")
                if key and key not in output_props:
                    output_props[key] = {"type": str(field.get("type") or "string")}
                    applied.append({"op": "add_output_schema_field", "capability": name, "field": key})
                if key and field.get("required") and key not in output_required:
                    output_required.append(key)
            clean_output_required = [key for key in output_required if key in output_props]
            if output_schema.get("required") != clean_output_required:
                output_schema["required"] = clean_output_required
                applied.append({"op": "sync_output_schema_required", "capability": name})
        if not cap.get("output_mapping"):
            returns = [x for x in (contract.get("return") or []) if isinstance(x, dict)]
            if returns:
                cap["output_mapping"] = copy.deepcopy(returns)
                applied.append({"op": "restore_output_mapping", "capability": name})
            else:
                calls = [x for x in (contract.get("call_order") or []) if isinstance(x, dict) and x.get("step_id")]
                if calls:
                    cap["output_mapping"] = [{"kind": "final_response", "step_id": calls[-1]["step_id"],
                                                "response_path": "response"}]
                    applied.append({"op": "derive_output_mapping", "capability": name})
        else:
            calls = [x for x in (contract.get("call_order") or []) if isinstance(x, dict) and x.get("step_id")]
            valid_steps = {str(x.get("step_id")) for x in calls}
            invalid = any(
                not isinstance(mapping, dict)
                or (mapping.get("step_id") and str(mapping.get("step_id")) not in valid_steps)
                for mapping in cap.get("output_mapping") or []
            )
            if invalid and calls:
                cap["output_mapping"] = [{"kind": "final_response", "step_id": calls[-1]["step_id"],
                                            "response_path": "response"}]
                applied.append({"op": "repair_output_mapping", "capability": name})

    relations = apir.get("capability_relations")
    if isinstance(relations, list):
        kept = []
        for relation in relations:
            dangling = not isinstance(relation, dict) or str(relation.get("from_capability") or "") not in refs \
                or str(relation.get("to_capability") or "") not in refs
            invalid_contract = False
            if isinstance(relation, dict) and not dangling:
                relation_kind = str(relation.get("mode") or relation.get("type") or "").strip().lower()
                has_field_mapping = bool(relation.get("from_output") and relation.get("to_input"))
                if relation_kind in {"external_transform", "data_mapping", "field_mapping"} or has_field_mapping:
                    source = next((c for c in capabilities if str(relation.get("from_capability")) in _capability_refs(c)), None)
                    target = next((c for c in capabilities if str(relation.get("to_capability")) in _capability_refs(c)), None)
                    source_type = _field_type(source or {}, str(relation.get("from_output") or ""), output=True)
                    target_type = _field_type(target or {}, str(relation.get("to_input") or ""), output=False)
                    invalid_contract = not source_type or not target_type or source_type != target_type
            if (dangling or invalid_contract) and (not isinstance(relation, dict) or not relation.get("confirmed")):
                applied.append({"op": "drop_invalid_relation",
                                "relation_id": relation.get("relation_id") if isinstance(relation, dict) else None})
                continue
            kept.append(relation)
        apir["capability_relations"] = kept
        graph = apir.get("capability_graph")
        if isinstance(graph, dict):
            graph["relations"] = copy.deepcopy(kept)

    goal = apir.get("goal")
    if isinstance(goal, dict) and isinstance(goal.get("capabilities"), list):
        actual = [str(c.get("name")) for c in capabilities if c.get("name")]
        synced = [name for name in goal["capabilities"] if str(name) in refs]
        synced.extend(name for name in actual if name not in synced)
        if synced != goal["capabilities"]:
            goal["capabilities"] = synced
            applied.append({"op": "sync_goal_capabilities"})
    return apir, applied


def _fix_target(apir, step=None):
    """操作目标:工作流取指定步(默认最后一步=提交那步),单请求取自身。"""
    steps = apir.get("steps")
    if steps:
        i = step if isinstance(step, int) else len(steps) - 1
        return steps[i] if 0 <= i < len(steps) else None
    return apir


def apply_fix_ops(api_request: dict, ops: list[dict]) -> tuple[dict, list, list]:
    """**确定性**执行 LLM 出的修复操作(受限词表 _FIX_OPS);引用必须真实存在,否则该操作被拒(不执行)。
    返回 (新 api_request, applied, rejected)。调用方应在其后重跑 self_check 复验。"""
    apir = copy.deepcopy(api_request)
    applied, rejected = [], []
    for op in (ops or []):
        before = copy.deepcopy(apir)
        base = set(self_check(apir))                       # 改前结构基线
        ok, detail = _apply_fix_one(apir, op)
        if not ok:
            rejected.append({**op, "ok": False, "detail": detail})
            continue
        new_bad = set(self_check(apir)) - base             # 这步是否**引入新结构问题**
        if new_bad:                                        # 坏操作 → **逐 op 回滚**(执行后立刻 self_check)
            apir = before
            rejected.append({**op, "ok": False, "detail": "回滚(引入结构问题):" + "; ".join(list(new_bad)[:2])})
        else:
            applied.append({**op, "ok": True, "detail": detail})
    return apir, applied, rejected


def _apply_fix_one(apir, op) -> tuple[bool, str]:  # noqa: C901
    name = op.get("op")
    if name not in _FIX_OPS:
        return False, f"未知操作 {name}"
    steps = apir.get("steps")
    if name == "drop_step":
        if not steps:
            return False, "无 steps"
        i = op.get("step")
        if not (isinstance(i, int) and 0 <= i < len(steps)):
            return False, "step 越界"
        del steps[i]
        for st in steps:                                       # 调整/丢弃受影响的 link
            if st.get("links"):
                nl = []
                for lk in st["links"]:
                    ss = lk.get("source_step")
                    if ss == i:
                        continue
                    if isinstance(ss, int) and ss > i:
                        lk = {**lk, "source_step": ss - 1}
                    nl.append(lk)
                st["links"] = nl
        return True, "ok"
    if name == "reorder_steps":
        if not steps:
            return False, "无 steps"
        order = op.get("order")
        if not (isinstance(order, list) and sorted(order) == list(range(len(steps)))):
            return False, "order 非合法排列"
        old = list(steps)
        pos = {old_i: new_i for new_i, old_i in enumerate(order)}
        steps[:] = [old[k] for k in order]
        for st in steps:
            for lk in (st.get("links") or []):
                if isinstance(lk.get("source_step"), int):
                    lk["source_step"] = pos.get(lk["source_step"], lk["source_step"])
        return True, "ok"
    if name == "set_success_rule":
        tgt = _fix_target(apir, op.get("step"))
        if tgt is None:
            return False, "无目标步"
        tgt["success_rule"] = {"field": op.get("field"), "ok_values": list(op.get("ok_values") or [])}
        return True, "ok"
    if name == "parameterize":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        toks, pname = op.get("path"), (op.get("param") or op.get("param_name"))
        if templ is None or not pname:
            return False, "缺 body_template/param"
        cur = _path_lookup(templ, toks)
        if cur is _PATH_MISSING:
            return False, "path 不存在"
        _set_by_path(templ, toks, "{{" + pname + "}}")
        if pname not in tgt.setdefault("params", []):
            tgt["params"].append(pname)
        tgt.setdefault("sample_inputs", {})[pname] = "" if cur is None else str(cur)
        return True, "ok"
    if name == "link_step":
        if not steps:
            return False, "无 steps(单请求不能串联)"
        ti, si = op.get("target_step"), op.get("source_step")
        if not (isinstance(ti, int) and 0 <= ti < len(steps)):
            return False, "target_step 越界"
        if not (isinstance(si, int) and 0 <= si < ti):
            return False, "source_step 须在 target 之前"
        tp = op.get("target_path")
        if _path_lookup(steps[ti].get("body_template"), tp) is _PATH_MISSING:
            return False, "target_path 不存在"
        sp = op.get("source_path")
        if not sp:                                         # source_path 必填,且要在来源步响应里真实存在
            return False, "缺 source_path"
        src_resp = steps[si].get("response_json")
        if src_resp is not None and _path_lookup(src_resp, sp) is _PATH_MISSING:
            return False, "source_path 在来源步响应里不存在(引用必须真实)"
        steps[ti].setdefault("links", []).append({
            "target_path": _tokens_to_str(tp) if isinstance(tp, list) else tp,
            "target_tokens": tp if isinstance(tp, list) else _split_path(tp),
            "source_step": si,
            "source_path": _tokens_to_str(sp) if isinstance(sp, list) else sp,
            "source_tokens": sp if isinstance(sp, list) else _split_path(sp)})
        return True, "ok"
    if name == "bind_placeholder":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        param, tp = op.get("param"), op.get("target_path")
        if templ is None or not param:
            return False, "缺 body_template/param"
        if _path_lookup(templ, tp) is _PATH_MISSING:
            return False, "target_path 不存在"
        old = _find_param_tokens(templ, param)             # 把占位绑到 target_path;清掉它在别处的占位(避免一参填多处)
        _set_by_path(templ, tp, "{{" + param + "}}")
        tp_toks = tp if isinstance(tp, list) else _split_path(tp)
        if old is not None and list(old) != list(tp_toks):
            _set_by_path(templ, old, "")
        if param not in tgt.setdefault("params", []):
            tgt["params"].append(param)
        return True, "ok"
    if name == "rename_param":
        old, new = (op.get("old") or op.get("param")), op.get("new")
        if not old or not new:
            return False, "缺 old/new"
        hit = False
        for tgt in (steps or [apir]):
            templ = tgt.get("body_template")
            if isinstance(templ, (dict, list)):
                toks = _find_param_tokens(templ, old)
                if toks is not None:
                    _set_by_path(templ, toks, "{{" + new + "}}")
                    hit = True
            if old in (tgt.get("params") or []):
                tgt["params"] = [new if p == old else p for p in tgt["params"]]
                hit = True
            for k in ("sample_inputs", "field_types"):
                if tgt.get(k) and old in tgt[k]:
                    tgt[k][new] = tgt[k].pop(old)
        return (True, "ok") if hit else (False, f"参数 {old} 不存在")
    if name == "remap_field":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        param, tp = op.get("param"), op.get("target_path")
        if templ is None:
            return False, "无 body_template"
        if param not in (tgt.get("params") or []):
            return False, f"param {param} 不存在"
        if _path_lookup(templ, tp) is _PATH_MISSING:
            return False, "target_path 不存在"
        old_toks = _find_param_tokens(templ, param)
        target_old = _path_lookup(templ, tp)
        _set_by_path(templ, tp, "{{" + param + "}}")
        tp_toks = tp if isinstance(tp, list) else _split_path(tp)
        if old_toks is not None and list(old_toks) != list(tp_toks):
            _set_by_path(templ, old_toks, target_old)          # 交换:旧位置放 target 的旧值(治字段错配/互换)
        return True, "ok"
    if name == "set_identity":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        p = op.get("path")
        if templ is None or _path_lookup(templ, p) is _PATH_MISSING:
            return False, "path 不存在"
        toks = p if isinstance(p, list) else _split_path(p)
        tgt.setdefault("identity", []).append(
            {"path": _tokens_to_str(toks), "tokens": list(toks), "source": op.get("source", "")})
        return True, "ok"
    return False, "未处理"
