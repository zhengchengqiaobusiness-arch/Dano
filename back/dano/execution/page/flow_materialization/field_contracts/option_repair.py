"""Stage 5: structural option-binding repair after capture."""
from __future__ import annotations

from typing import Any
import json
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.request_capture import (
    _is_idlike,
    _pick_label_key,
    as_list_payload,
)
from dano.execution.page.recording_facts import (
    _SCREENSHOT_OPTION_CONTROL_KINDS,
    _choice_control_triggered,
    _list_payload_has_reference_contract,
    _looks_pagination_field,
    _page_enum_options_from_request_facts,
    _read_is_entity_enrichment_lookup,
    _request_has_option_endpoint_hint,
    _request_has_reference_entity_hint,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _looks_catalog_attribute_leaf,
    _looks_display_echo_field,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_user_entered_business_field,
    _param_group_prefix,
    _param_has_grounded_direct_input_contract,
    _param_has_manual_contract,
    _screenshot_control_evidence,
    _screenshot_control_supports_axis,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _apply_selected_option_field_caller_ownership,
    _param_has_editable_control_evidence,
)


def _option_binding_tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*|[\u4e00-\u9fff]+", text)
        if token
    }
    expanded = set(tokens)
    expanded.update(token[:-1] for token in tokens if token.endswith("s") and len(token) > 3)
    return expanded - {
        "id", "ids", "code", "key", "value", "values", "data", "api", "admin",
        "list", "simple", "options", "option", "query", "page", "get",
        # These words describe generic payloads/endpoints and cannot establish
        # that a caller-entered field owns an option source.
        "info", "information", "name", "title", "label", "text",
        # Generic classifier leaves occur in unrelated background endpoints
        # (for example presence/status traffic). They need field-local control
        # ownership rather than token equality before becoming an option API.
        "status", "statu", "state", "type", "kind", "category", "flag", "result",
    }


def _weak_automatic_text_option_binding(param: ParamField) -> bool:
    """Identify an ungrounded automatic option binding on a text-like field.

    This intentionally does not override manual edits or a recorded select;
    callers enforce edit ownership separately.  It only marks bindings whose
    endpoint has no semantic bridge to the field after generic tokens such as
    ``info`` have been removed.
    """
    if (
        param.source_kind != "api_option"
        or not _looks_user_entered_business_field(param.key, param.path)
    ):
        return False
    option_controls = {
        "select", "combobox", "cascader", "picker", "radio", "tree_select",
    }
    if any(
        isinstance(item, dict)
        and str(item.get("control_kind") or "").strip().lower() in option_controls
        for item in (param.evidence or [])
    ):
        return False
    target_text = " ".join((str(param.path or ""), str(param.key or ""), str(param.label or "")))
    source_text = str((param.source or {}).get("source_url") or "")
    if not source_text:
        return False
    return not bool(
        (_option_binding_tokens(target_text) & _option_binding_tokens(source_text))
        or (
            _option_binding_semantic_families(target_text)
            & _option_binding_semantic_families(source_text)
        )
    )


def _option_binding_semantic_families(value: Any) -> set[str]:
    text = str(value or "").casefold()
    families: set[str] = set()
    patterns = {
        "person": r"(?:user|users|assignee|approver|reviewer|auditor|employee|member|person|people|creator|createdby|author|审批|审核|人员|用户|负责人|创建人|创建者)",
        "organization": r"(?:dept|department|org|organization|division|unit|部门|组织|机构)",
        "team": r"(?:team|group|squad|团队|班组|小组)",
        "project": r"(?:project|initiative|项目)",
        "role": r"(?:role|position|post|岗位|角色|职位)",
        "customer": r"(?:customer|client|buyer|客户|购买方)",
        "supplier": r"(?:supplier|vendor|provider|供应商|供方)",
    }
    for family, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            families.add(family)
    return families


def _looks_quantitative_option_target(param: ParamField) -> bool:
    """Counts and measurements are scalar inputs, not foreign-key choices."""
    text = " ".join((str(param.path or ""), str(param.key or ""), str(param.label or "")))
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).casefold()
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).casefold()
    if re.search(r"(?:id|ids|code|key)$", compact):
        return False
    numeric_wire = str(param.wire_type or param.type or "").lower() in {
        "number", "integer", "float", "decimal",
    }
    return bool(
        re.search(r"(?:^|\W)(?:count|qty|quantity|amount|total|capacity)(?:$|\W)", split)
        or (
            numeric_wire
            and re.search(r"(?:^|\W)(?:num|number)(?:$|\W)", split)
        )
        or re.search(r"(?:人数|数量|个数|金额|总数|总量|容量|次数|时长|天数)", text)
    )


def _independent_option_request_ids(param: ParamField) -> set[str]:
    """Return request identities proved by the owning control, not inference."""
    owned: set[str] = set()
    for item in param.evidence or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "page_control":
            continue
        if str(item.get("binding_status") or "bound") != "bound":
            continue
        values = item.get("source_request_ids") or []
        if not isinstance(values, list):
            values = [values]
        values = [*values, item.get("source_request_id")]
        owned.update(str(value) for value in values if value)
    return owned


def _option_control_context(param: ParamField) -> tuple[set[str], set[str]]:
    transactions: set[str] = set()
    actions: set[str] = set()
    for item in param.evidence or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "page_control":
            continue
        if str(item.get("binding_status") or "bound") != "bound":
            continue
        transaction_id = str(
            item.get("trigger_transaction_id") or item.get("transaction_id") or ""
        )
        action_id = str(item.get("trigger_action_id") or item.get("action_id") or "")
        if transaction_id:
            transactions.add(transaction_id)
        if action_id:
            actions.add(action_id)
    return transactions, actions


def _source_owned_by_param(source: dict[str, Any], param: ParamField) -> bool:
    request_id = str(source.get("source_request_id") or "")
    if request_id and request_id in _independent_option_request_ids(param):
        return True
    transactions, actions = _option_control_context(param)
    source_transaction = str(source.get("trigger_transaction_id") or "")
    source_action = str(source.get("trigger_action_id") or "")
    return bool(
        (source_transaction and source_transaction in transactions)
        or (source_action and source_action in actions)
    )


def _clear_ambiguous_automatic_option_request_ids(spec: FlowSpec) -> None:
    """Drop false precision when repeated captures share one source contract."""
    facts_by_endpoint: dict[str, list[Any]] = {}
    facts_by_request_id: dict[str, Any] = {}
    for fact in spec.request_facts.requests or []:
        endpoint = _option_source_contract_endpoint(str(fact.url or fact.path or ""))
        if endpoint and fact.request_id:
            facts_by_endpoint.setdefault(endpoint, []).append(fact)
            facts_by_request_id[str(fact.request_id)] = fact
    request_id_by_step_id = {
        str(step.step_id): str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
        if step.step_id
    }
    exact_request_ids_by_target: dict[str, set[str]] = {}
    for capability in spec.capabilities or []:
        exact_request_ids = {
            str(ref.request_id or "")
            for ref in capability.request_refs or []
            if ref.usage == "option_source" and str(ref.request_id or "")
        }
        exact_request_ids.update(
            request_id_by_step_id.get(str(ref.step_id or ""), "")
            for ref in capability.request_refs or []
            if ref.usage == "option_source" and str(ref.step_id or "")
        )
        exact_request_ids.discard("")
        for target_step_id in capability.step_ids or []:
            exact_request_ids_by_target.setdefault(str(target_step_id), set()).update(
                exact_request_ids
            )
    for step in spec.steps:
        step_meta = step.source_meta or {}
        for param in step.params or []:
            if param.source_kind != "api_option" or _param_has_manual_contract(param):
                continue
            source = dict(param.source or {})
            request_id = str(source.get("source_request_id") or "")
            endpoint = _option_source_contract_endpoint(str(source.get("source_url") or ""))
            if not request_id or not endpoint or _independent_option_request_ids(param):
                continue
            exact_same_endpoint = {
                exact_request_id
                for exact_request_id in exact_request_ids_by_target.get(str(step.step_id), set())
                if exact_request_id in facts_by_request_id
                and _option_source_contract_endpoint(str(
                    facts_by_request_id[exact_request_id].url
                    or facts_by_request_id[exact_request_id].path
                    or ""
                )) == endpoint
            }
            if exact_same_endpoint == {request_id}:
                # The capability plan narrowed this endpoint to one recorded
                # request. Repeated observations outside that exact cohort do
                # not make the selected source identity ambiguous.
                continue
            candidates = []
            for fact in facts_by_endpoint.get(endpoint, []):
                if (
                    fact.page_id and step_meta.get("page_id")
                    and str(fact.page_id) != str(step_meta.get("page_id"))
                ):
                    continue
                if (
                    fact.frame_id and step_meta.get("frame_id")
                    and str(fact.frame_id) != str(step_meta.get("frame_id"))
                ):
                    continue
                candidates.append(fact)
            if len({str(fact.request_id) for fact in candidates}) <= 1:
                continue
            param.source = {**source, "source_request_id": ""}
            for binding in step.selects or []:
                if _strip_body_prefix(binding.path or binding.id_path or "") == _strip_body_prefix(param.path):
                    binding.source_request_id = ""


def _restore_executable_option_request_ids(spec: FlowSpec) -> int:
    """Attach a concrete captured occurrence to URL-grounded option contracts.

    Repeated option loads share one renewable endpoint contract. Clearing a
    false exact occurrence is correct, but leaving both request and step IDs
    empty makes an otherwise executable source fail validation. For a target
    request, the nearest matching occurrence at or before that request is the
    page's active catalog snapshot; later dialog loads belong to later actions.
    """
    step_id_by_request_id = {
        str((step.source_meta or {}).get("request_id") or ""): step.step_id
        for step in spec.steps
        if str((step.source_meta or {}).get("request_id") or "")
    }

    def sequence(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0

    def target_sequence(step: FlowStep) -> float:
        meta = step.source_meta or {}
        return sequence(meta.get("sequence", meta.get("request_index")))

    repaired = 0
    for target in spec.steps:
        target_meta = target.source_meta or {}
        target_pos = target_sequence(target)
        for param in target.params or []:
            source = dict(param.source or {})
            contracts: list[tuple[dict[str, Any], bool]] = []
            if param.source_kind == "api_option":
                contracts.append((source, False))
            nested = source.get("option_source")
            if isinstance(nested, dict) and str(nested.get("kind") or "") == "api_option":
                contracts.append((dict(nested), True))
            for contract, nested_contract in contracts:
                if (
                    contract.get("source_request_id")
                    or contract.get("source_step_id")
                    or not contract.get("source_url")
                    or not contract.get("value_key")
                    or not contract.get("label_key")
                ):
                    continue
                endpoint = _option_source_contract_endpoint(
                    str(contract.get("source_url") or "")
                )
                candidates: list[tuple[float, Any]] = []
                for fact in spec.request_facts.requests or []:
                    if str(fact.method or "GET").upper() not in {"GET", "HEAD"}:
                        continue
                    if _option_source_contract_endpoint(
                        str(fact.url or fact.path or "")
                    ) != endpoint:
                        continue
                    if (
                        fact.page_id and target_meta.get("page_id")
                        and str(fact.page_id) != str(target_meta.get("page_id"))
                    ):
                        continue
                    if (
                        fact.frame_id and target_meta.get("frame_id")
                        and str(fact.frame_id) != str(target_meta.get("frame_id"))
                    ):
                        continue
                    rows = [
                        item for item in (as_list_payload(fact.response_json) or [])
                        if isinstance(item, dict)
                    ]
                    category_key = str(contract.get("category_key") or "")
                    category_value = contract.get("category_value")
                    if category_key and category_value not in (None, ""):
                        rows = [
                            item for item in rows
                            if str(item.get(category_key)) == str(category_value)
                        ]
                    if not any(
                        contract["value_key"] in item and contract["label_key"] in item
                        for item in rows
                    ):
                        continue
                    fact_pos = sequence(
                        fact.sequence if fact.sequence is not None else fact.request_index
                    )
                    candidates.append((fact_pos, fact))
                if not candidates:
                    continue
                before = [item for item in candidates if item[0] <= target_pos]
                _chosen_pos, chosen = (
                    max(before, key=lambda item: item[0])
                    if before else min(candidates, key=lambda item: item[0])
                )
                request_id = str(chosen.request_id or "")
                if not request_id:
                    continue
                contract = {
                    **contract,
                    "source_request_id": request_id,
                    "source_step_id": step_id_by_request_id.get(request_id, ""),
                }
                if nested_contract:
                    param.source = {**source, "option_source": contract}
                    source = dict(param.source)
                else:
                    param.source = contract
                    source = dict(contract)
                binding = _find_select_binding(target, param)
                if binding is not None:
                    binding.source_request_id = request_id
                repaired += 1
    return repaired


def _repair_structural_option_bindings(
    spec: FlowSpec, *, modern_only: bool = False,
) -> int:
    """Recover grounded enum/reference bindings, including captured-only reads.

    Candidate selection is evidence based: an exact recorded wire value, a real
    ID/display row contract, and either control ownership, a shared semantic
    family, or an exact field token are required. Shared dictionary endpoints
    are narrowed only when the page's visible labels identify one category.
    Repeated captures of the same endpoint/contract are one source, not an
    ambiguity.
    """
    if (
        modern_only
        and int((spec.meta or {}).get("stage_1_6_contract_version") or 0) < 2
    ):
        return 0
    _clear_ambiguous_automatic_option_request_ids(spec)
    candidates: list[dict[str, Any]] = []
    materialized_request_ids: set[str] = set()
    for source in spec.steps:
        role = str((source.source_meta or {}).get("role") or source.semantic_role or "")
        request_id = str((source.source_meta or {}).get("request_id") or "")
        read = {
            "url": source.url or source.path,
            "path": source.path,
            "method": source.method,
            "role": role,
            "response_json": source.response_json,
            "post_data": source.body_source,
            **dict(source.source_meta or {}),
        }
        items = as_list_payload(source.response_json) or []
        entity_collection_source = _read_is_business_entity_collection(
            read, source.response_json,
        )
        if (
            _read_transport_can_supply_options(read)
            and items
            and all(isinstance(item, dict) for item in items)
            and not _read_is_entity_enrichment_lookup(read)
            and (
                _read_is_option_source(read)
                or entity_collection_source
                or (
                    _list_payload_has_reference_contract(source.response_json)
                    and (
                        _request_has_option_endpoint_hint(read)
                        or _request_has_reference_entity_hint(read)
                    )
                )
            )
        ):
            candidates.append({
                "source_step_id": source.step_id,
                "source_request_id": request_id,
                "source_url": source.url or source.path,
                "sequence": (source.source_meta or {}).get("sequence", (source.source_meta or {}).get("request_index")),
                "page_id": (source.source_meta or {}).get("page_id"),
                "frame_id": (source.source_meta or {}).get("frame_id"),
                "trigger_action_id": (source.source_meta or {}).get("trigger_action_id"),
                "trigger_transaction_id": (source.source_meta or {}).get("trigger_transaction_id"),
                "explicit_option_source": role in {
                    "read_option", "option_source", "explicit_read_option",
                } or _choice_control_triggered(read),
                "entity_collection_source": entity_collection_source,
                "items": [dict(item) for item in items if isinstance(item, dict)],
            })
            if request_id:
                materialized_request_ids.add(request_id)

    # Reuse an already confirmed binding as ordinary candidate evidence. The
    # existing unique matcher can then ground repeated sibling fields even when
    # their projected request rows were truncated.
    for source in spec.steps:
        for binding in source.selects or []:
            option_map = dict(binding.option_map or {})
            if not (
                binding.enum_confirmed is True
                and binding.source_url and binding.value_key and binding.label_key
                and len(option_map) >= 2
            ):
                continue
            owner = next((
                param for param in (source.params or [])
                if _strip_body_prefix(param.path or "")
                == _strip_body_prefix(binding.path or binding.id_path or "")
            ), None)
            if owner is not None and _param_has_grounded_direct_input_contract(owner):
                continue
            owner_source = dict((owner.source if owner is not None else None) or {})
            candidates.append({
                "source_step_id": str(owner_source.get("source_step_id") or ""),
                "source_request_id": str(owner_source.get("source_request_id") or ""),
                "source_url": binding.source_url,
                "sequence": (source.source_meta or {}).get("sequence"),
                "page_id": (source.source_meta or {}).get("page_id"),
                "frame_id": (source.source_meta or {}).get("frame_id"),
                "trigger_action_id": (source.source_meta or {}).get("trigger_action_id"),
                "trigger_transaction_id": (source.source_meta or {}).get("trigger_transaction_id"),
                "items": [
                    {binding.value_key: value, binding.label_key: label}
                    for label, value in option_map.items()
                ],
            })

    for fact in (spec.request_facts.requests or []):
        request_id = str(fact.request_id or "")
        if request_id and request_id in materialized_request_ids:
            continue
        analysis = spec.request_facts.analysis.get(request_id) if request_id else None
        read = fact.model_dump(exclude_none=True)
        read["role"] = str(analysis.role if analysis is not None else read.get("role") or "")
        items = as_list_payload(fact.response_json) or []
        entity_collection_source = _read_is_business_entity_collection(
            read, fact.response_json,
        )
        if (
            _read_transport_can_supply_options(read)
            and items
            and all(isinstance(item, dict) for item in items)
            and not _read_is_entity_enrichment_lookup(read)
            and (
                _read_is_option_source(read)
                or entity_collection_source
                or (
                    _list_payload_has_reference_contract(fact.response_json)
                    and (
                        _request_has_option_endpoint_hint(read)
                        or _request_has_reference_entity_hint(read)
                    )
                )
            )
        ):
            candidates.append({
                "source_step_id": "",
                "source_request_id": request_id,
                "source_url": fact.url or fact.path,
                "sequence": fact.sequence if fact.sequence is not None else fact.request_index,
                "page_id": fact.page_id,
                "frame_id": fact.frame_id,
                "trigger_action_id": getattr(fact, "trigger_action_id", None),
                "trigger_transaction_id": getattr(fact, "trigger_transaction_id", None),
                "explicit_option_source": read["role"] in {
                    "read_option", "option_source", "explicit_read_option",
                } or _choice_control_triggered(read),
                "entity_collection_source": entity_collection_source,
                "items": [dict(item) for item in items if isinstance(item, dict)],
            })

    exact_option_scope_by_target: dict[str, tuple[set[str], set[str]]] = {}
    for capability in spec.capabilities or []:
        request_ids = {
            str(ref.request_id or "")
            for ref in capability.request_refs or []
            if ref.usage == "option_source" and str(ref.request_id or "")
        }
        step_ids = {
            str(ref.step_id or "")
            for ref in capability.request_refs or []
            if ref.usage == "option_source" and str(ref.step_id or "")
        }
        if not request_ids and not step_ids:
            continue
        for target_step_id in capability.step_ids or []:
            current_request_ids, current_step_ids = exact_option_scope_by_target.setdefault(
                target_step_id, (set(), set()),
            )
            current_request_ids.update(request_ids)
            current_step_ids.update(step_ids)

    repaired = 0

    def has_screenshot_choice(param: ParamField) -> bool:
        control = _screenshot_control_evidence({"evidence": param.evidence})
        return bool(
            control is not None
            and (
                _screenshot_control_supports_axis(control, "type")
                or _screenshot_control_supports_axis(control, "source")
            )
            and str(control.get("control_kind") or "").lower()
            in _SCREENSHOT_OPTION_CONTROL_KINDS
        )

    def has_recorded_choice(param: ParamField) -> bool:
        return has_screenshot_choice(param) or any(
            isinstance(item, dict)
            and str(item.get("source") or item.get("kind") or "").lower()
            in {
                "recorder_dom", "recorded_parallel_form",
                "page", "page_snapshot", "page_control",
            }
            and str(item.get("control_kind") or "").lower()
            in _SCREENSHOT_OPTION_CONTROL_KINDS
            for item in (param.evidence or [])
        )

    for target in spec.steps:
        direct_input_paths: set[str] = set()
        for param in target.params or []:
            if (
                param.source_kind not in _OPTION_SOURCE_KINDS
                or param.locked
                or _param_has_manual_contract(param)
                or not _param_has_grounded_direct_input_contract(param)
            ):
                continue
            param.type = str(param.wire_type or "string")
            param.source_kind = "user_input"
            param.source = {"kind": "user_input", "path": param.path}
            param.enum_options = None
            param.enum_value_map = None
            _refresh_param_enum_description(param)
            direct_input_paths.add(_strip_body_prefix(param.path or ""))
            repaired += 1
        if direct_input_paths:
            target.selects = [
                binding for binding in (target.selects or [])
                if _strip_body_prefix(binding.path or binding.id_path or "") not in direct_input_paths
            ]

    page_options = _page_enum_options_from_request_facts(spec.request_facts)

    def normalized(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()

    def normalized_control_name(value: Any) -> str:
        # Recorder duplicate-label ordinals distinguish snapshot entries, not
        # business fields.  They may be ignored only for a semantic join that
        # is subsequently proven by one bound control path and option rows.
        return normalized(re.sub(r"#\d+$", "", str(value or "").strip()))

    def option_labels(raw: dict[str, Any]) -> set[str]:
        labels = set()
        for option in list(raw.get("options") or raw.get("values") or []):
            pair = _enum_label_value(option)
            label = str(pair[0] if pair is not None else "").strip()
            if label:
                labels.add(label)
        return labels

    eligible_values = [
        str(param.value if param.value is not None else "").strip()
        for step in spec.steps
        for param in (step.params or [])
        if not param.locked
        and not _param_has_manual_contract(param)
        and not _param_has_grounded_direct_input_contract(param)
        and param.category != "system_const"
        and str(param.value if param.value is not None else "").strip()
    ]
    value_owner_counts = {value: eligible_values.count(value) for value in set(eligible_values)}

    def page_evidence_for(target: FlowStep, param: ParamField, value: str) -> list[dict[str, Any]]:
        param_names = {
            normalized(name)
            for name in (
                param.path, _strip_body_prefix(param.path or ""),
                _strip_body_prefix(param.path or "").split(".")[-1],
                param.key, param.label,
            )
            if normalized(name)
        }
        for evidence in param.evidence or []:
            if not isinstance(evidence, dict):
                continue
            param_names.update(
                normalized(str(alias).split(":", 1)[-1])
                for alias in (evidence.get("field_aliases") or [])
                if normalized(str(alias).split(":", 1)[-1])
            )
        bound_control_ids = {
            str(evidence.get(key) or "")
            for evidence in (param.evidence or [])
            if isinstance(evidence, dict)
            and evidence.get("kind") == "page_control"
            and str(evidence.get("binding_status") or "bound") == "bound"
            for key in ("evidence_id", "field_identity_id", "occurrence_id")
            if str(evidence.get(key) or "")
        }
        screenshot_matches: list[dict[str, Any]] = []
        screenshot_evidence = _screenshot_control_evidence({"evidence": param.evidence})
        for evidence in [screenshot_evidence] if screenshot_evidence is not None else []:
            if str(evidence.get("control_kind") or "").lower() not in _SCREENSHOT_OPTION_CONTROL_KINDS:
                continue
            labels = option_labels({"options": evidence.get("options") or []})
            if len(labels) < 2:
                continue
            visible_value = str(evidence.get("visible_value") or "").strip()
            screenshot_matches.append({
                "raw": evidence,
                "labels": labels,
                "selected": visible_value if visible_value in labels else "",
                "semantic_match": True,
            })
        if screenshot_matches:
            return screenshot_matches
        semantic_matches: list[dict[str, Any]] = []
        fallback_matches: list[dict[str, Any]] = []
        candidate_set_matches: list[dict[str, Any]] = []
        target_meta = target.source_meta or {}
        for raw_key, raw_value in page_options.items():
            if not isinstance(raw_value, dict):
                continue
            raw = dict(raw_value)
            labels = option_labels(raw)
            if len(labels) < 2 or str(raw.get("control_kind") or "select").lower() != "select":
                continue
            if target_meta.get("page_id") and raw.get("page_id") and str(target_meta.get("page_id")) != str(raw.get("page_id")):
                continue
            raw_names = {
                normalized_control_name(str(name).split(":", 1)[-1])
                for name in (raw_key, raw.get("field_key"), *(raw.get("field_aliases") or []))
                if normalized_control_name(str(name).split(":", 1)[-1])
            }
            contract = {
                "raw": raw,
                "labels": labels,
                "selected": str(raw.get("selected_label") or raw.get("selected") or "").strip(),
                "semantic_match": bool(param_names & raw_names),
            }
            if param_names & raw_names:
                semantic_matches.append(contract)
            elif value_owner_counts.get(value) == 1 and contract["selected"]:
                fallback_matches.append(contract)
            elif (
                not bool(raw.get("snapshot_truncated") or raw.get("truncated"))
                and bool(bound_control_ids & {
                    str(raw.get(key) or "")
                    for key in ("evidence_id", "field_identity_id", "occurrence_id")
                    if str(raw.get(key) or "")
                })
            ):
                # No field name/selection survived, but the complete visible
                # candidate set can still identify its source after immutable
                # control identity proves that this enum belongs to this exact
                # request field. The label-set equality check in row_contracts
                # remains mandatory before this becomes a binding.
                candidate_set_matches.append({**contract, "candidate_set_bridge": True})
        return semantic_matches or fallback_matches or candidate_set_matches

    def row_contracts(
        items: list[dict[str, Any]],
        value: str,
        page_contract: dict[str, Any] | None,
        *,
        allow_single: bool = False,
    ) -> list[dict[str, Any]]:
        matching_items = [
            item for item in items
            if any(_is_idlike(str(key)) and str(item_value) == value for key, item_value in item.items())
        ]
        value_keys = {
            str(key)
            for item in matching_items
            for key, item_value in item.items()
            if _is_idlike(str(key)) and str(item_value) == value
        }
        contracts: list[dict[str, Any]] = []
        visible_labels = set(page_contract.get("labels") or set()) if page_contract else set()
        selected_label = str(page_contract.get("selected") or "") if page_contract else ""
        for value_key in value_keys:
            selected_for_value_key = [
                item for item in matching_items
                if str(item.get(value_key)) == value
            ]
            label_keys = {
                _pick_label_key(item, value_key)
                for item in selected_for_value_key
                if _pick_label_key(item, value_key) != value_key
            }
            for label_key in label_keys:
                subsets: list[tuple[str | None, Any, list[dict[str, Any]]]] = [(None, None, items)]
                if page_contract:
                    scalar_keys = {
                        str(key)
                        for item in items
                        for key, raw_value in item.items()
                        if key not in {value_key, label_key}
                        and not isinstance(raw_value, (dict, list))
                    }
                    for category_key in scalar_keys:
                        category_values = {
                            item.get(category_key)
                            for item in matching_items
                            if item.get(category_key) not in (None, "")
                            and not isinstance(item.get(category_key), (dict, list))
                        }
                        for category_value in category_values:
                            subset = [
                                item for item in items
                                if str(item.get(category_key)) == str(category_value)
                            ]
                            # A scalar repeated by every row is metadata, not a
                            # category (for example the same avatar URL on all
                            # users). Treating it as a filter creates a second,
                            # indistinguishable option contract and makes an
                            # otherwise unique source look ambiguous.
                            if 2 <= len(subset) < len(items):
                                subsets.append((category_key, category_value, subset))
                seen_subsets: set[str] = set()
                for category_key, category_value, subset in subsets:
                    subset_sig = json.dumps(
                        [category_key, category_value, subset],
                        ensure_ascii=False, sort_keys=True, default=str,
                    )
                    if subset_sig in seen_subsets:
                        continue
                    seen_subsets.add(subset_sig)
                    selected_rows = [item for item in subset if str(item.get(value_key)) == value]
                    if len(selected_rows) != 1:
                        continue
                    raw_records: list[tuple[str, Any]] = []
                    seen_values: set[str] = set()
                    valid = True
                    for item in subset:
                        label = str(item.get(label_key) or "").strip()
                        raw_value = item.get(value_key)
                        value_sig = str(raw_value)
                        if not label or raw_value in (None, "") or value_sig in seen_values:
                            valid = False
                            break
                        seen_values.add(value_sig)
                        raw_records.append((label, raw_value))
                    if not valid or len(raw_records) < (1 if allow_single else 2):
                        continue
                    label_counts = {
                        label: sum(1 for candidate, _ in raw_records if candidate == label)
                        for label, _ in raw_records
                    }
                    records: list[dict[str, Any]] = []
                    option_map: dict[str, Any] = {}
                    for label, raw_value in raw_records:
                        # Duplicate display names are legal in real option
                        # APIs.  Keep every wire value and make only the public
                        # choice label unambiguous instead of rejecting the
                        # entire source or silently mapping to the last row.
                        public_label = (
                            label
                            if label_counts[label] == 1
                            else f"{label} [{raw_value}]"
                        )
                        option_map[public_label] = raw_value
                        records.append({"label": public_label, "value": raw_value})
                    if page_contract:
                        record_labels = {label for label, _ in raw_records}
                        raw_page_contract = page_contract.get("raw") or {}
                        snapshot_truncated = bool(
                            raw_page_contract.get("snapshot_truncated")
                            or raw_page_contract.get("truncated")
                        )
                        if snapshot_truncated:
                            required_overlap = min(2, len(visible_labels))
                            if len(record_labels & visible_labels) < required_overlap:
                                continue
                        elif record_labels != visible_labels:
                            # A complete control snapshot is an exact option-set
                            # contract. Partial label overlap is common in
                            # unrelated business lists and cannot establish an
                            # option-source relationship.
                            continue
                        if selected_label and (
                            not any(
                                label == selected_label and str(raw_value) == value
                                for label, raw_value in raw_records
                            )
                        ):
                            continue
                    contracts.append({
                        "value_key": value_key,
                        "label_key": label_key,
                        "category_key": category_key,
                        "category_value": category_value,
                        "records": records,
                        "option_map": option_map,
                        "raw_labels": sorted({label for label, _ in raw_records}),
                    })
        if page_contract:
            exact_label_contracts = [
                contract for contract in contracts
                if set(contract.get("raw_labels") or []) == visible_labels
            ]
            if exact_label_contracts:
                return exact_label_contracts
        return contracts

    def source_is_grounded_for_target(
        source: dict[str, Any],
        target: FlowStep,
        page_contract: dict[str, Any] | None,
        semantic_match: bool,
    ) -> bool:
        """Require a causal, explicit, or semantic bridge; value equality is never enough."""
        source_tx = str(source.get("trigger_transaction_id") or "")
        source_action = str(source.get("trigger_action_id") or "")
        target_meta = target.source_meta or {}
        target_tx = str(target_meta.get("trigger_transaction_id") or "")
        target_action = str(target_meta.get("trigger_action_id") or "")
        source_page = str(source.get("page_id") or "")
        source_frame = str(source.get("frame_id") or "")
        target_page = str(target_meta.get("page_id") or "")
        target_frame = str(target_meta.get("frame_id") or "")
        ownership_scope: dict[str, Any] = {
            "page_id": target_page,
            "frame_id": target_frame,
        }
        if page_contract is not None:
            raw = page_contract.get("raw") or {}
            page_id = str(raw.get("page_id") or "")
            frame_id = str(raw.get("frame_id") or "")
            # A select's action/transaction identifies the caller's selection,
            # not necessarily the request that loaded its candidates. Many
            # applications preload option catalogs before the popup is opened.
            # Enforce transaction/action only when capture explicitly marked
            # them as source-request identity.
            page_tx = str(raw.get("source_transaction_id") or "")
            page_action = str(raw.get("source_action_id") or "")
            ownership_scope = {
                "page_id": page_id or target_page,
                "frame_id": frame_id or target_frame,
                **({"transaction_id": page_tx} if page_tx else {}),
                **({"action_id": page_action} if page_action else {}),
            }
        # Page/frame are an ownership scope, not a complete request identity.
        # Reusing the strict request identity matcher here made every
        # page-only scope fail because it intentionally requires method+URL.
        # Scope coordinates reject known conflicts; explicitly supplied
        # source action/transaction coordinates remain hard constraints.
        for scope_key in ("page_id", "frame_id"):
            expected = str(ownership_scope.get(scope_key) or "")
            actual = str(source.get(scope_key) or "")
            if expected and actual and expected != actual:
                return False
        for scope_key, source_key in (
            ("transaction_id", "trigger_transaction_id"),
            ("action_id", "trigger_action_id"),
        ):
            expected = str(ownership_scope.get(scope_key) or "")
            if expected and str(source.get(source_key) or "") != expected:
                return False
        if page_contract is not None and page_contract.get("candidate_set_bridge") is True:
            # row_contracts runs next and accepts this bridge only when the
            # complete DOM label set equals the candidate response label set.
            return True
        if semantic_match or bool((page_contract or {}).get("semantic_match")):
            # Field-local page evidence identifies the control. The caller's
            # row-contract check still has to prove that this candidate API
            # supplies the same visible option set and selected wire value.
            # This permits catalogs preloaded before the control interaction
            # without accepting an unrelated same-value request.
            return True
        same_action_context = bool(
            source_action
            and source_action == target_action
            and not (source_page and target_page and source_page != target_page)
            and not (source_frame and target_frame and source_frame != target_frame)
        )
        if (source_tx and source_tx == target_tx) or same_action_context:
            return True
        if page_contract is None:
            return False
        raw = page_contract.get("raw") or {}
        source_request_id = str(source.get("source_request_id") or "")
        page_request_ids = {
            str(item) for item in (raw.get("source_request_ids") or []) if item not in (None, "")
        }
        if source_request_id and source_request_id in page_request_ids:
            return True
        source_url = str(source.get("source_url") or "")
        page_source_urls = {
            str(item) for item in (
                raw.get("source_url"),
                *(raw.get("source_urls") or []),
            ) if item not in (None, "")
        }
        if source_url and source_url in page_source_urls:
            return True
        page_tx = str(raw.get("trigger_transaction_id") or raw.get("transaction_id") or "")
        page_action = str(raw.get("trigger_action_id") or raw.get("action_id") or "")
        page_id = str(raw.get("page_id") or "")
        frame_id = str(raw.get("frame_id") or "")
        return bool(
            (source_tx and source_tx == page_tx)
            or (
                source_action
                and source_action == page_action
                and not (source_page and page_id and source_page != page_id)
                and not (source_frame and frame_id and source_frame != frame_id)
            )
        )

    for target in spec.steps:
        exact_request_ids, exact_step_ids = exact_option_scope_by_target.get(
            target.step_id, (set(), set()),
        )
        has_exact_option_scope = bool(exact_request_ids or exact_step_ids)
        target_candidates = [
            source for source in candidates
            if not has_exact_option_scope
            or str(source.get("source_request_id") or "") in exact_request_ids
            or str(source.get("source_step_id") or "") in exact_step_ids
        ]
        for param in target.params or []:
            parallel_form_choice = any(
                isinstance(item, dict)
                and item.get("source") == "recorded_parallel_form"
                and str(item.get("control_kind") or "").lower()
                in _SCREENSHOT_OPTION_CONTROL_KINDS
                for item in (param.evidence or [])
            )
            param_candidates = (
                candidates
                if has_exact_option_scope and parallel_form_choice
                else target_candidates
            )
            source = dict(param.source or {})
            hydrated_option = (
                source.get("option_source")
                if isinstance(source.get("option_source"), dict) else {}
            )
            readonly_control = any(
                isinstance(item, dict)
                and item.get("kind") == "page_control"
                and bool(
                    item.get("disabled")
                    or item.get("read_only")
                    or item.get("editable") is False
                )
                for item in (param.evidence or [])
            )
            if (
                param.source_kind == "previous_response"
                and hydrated_option.get("source_url")
                and hydrated_option.get("value_key")
                and hydrated_option.get("label_key")
                and not readonly_control
                and not param.locked
                and not _param_has_manual_contract(param)
            ):
                # Edit hydration is a default-value layer. A captured option
                # contract on the same field proves that callers may retain or
                # replace that value using the same choices as Create.
                param.category = "user_param"
                param.type = "enum"
                param.editable = True
                param.exposed_to_user = True
                param.need_human_confirm = False
                param.source = {
                    **source,
                    "kind": "previous_response",
                    "allow_caller_override": True,
                    "option_source": hydrated_option,
                }
                source = dict(param.source)
                repaired += 1
            primary_source_request_ids = {
                str(value)
                for value in (
                    source.get("source_request_id"),
                    source.get("origin_request_id"),
                )
                if value
            }
            primary_source_step_id = str(source.get("source_step_id") or source.get("step_id") or "")
            if primary_source_step_id:
                primary_source_request_ids.update(
                    str(candidate.get("source_request_id") or "")
                    for candidate in candidates
                    if str(candidate.get("source_step_id") or "") == primary_source_step_id
                    and candidate.get("source_request_id")
                )
            displaced_automatic_option = bool(
                has_exact_option_scope
                and (
                    param.source_kind == "api_option"
                    or (
                        param.source_kind == "previous_response"
                        and has_recorded_choice(param)
                    )
                )
                and (
                    not primary_source_request_ids
                    or primary_source_request_ids.isdisjoint(exact_request_ids)
                )
            )
            leaf = str(param.path or param.key or "").replace("[]", "").split(".")[-1]
            selected_entity_target = bool(
                (
                    str(source.get("kind") or "") == "selected_entity_id"
                    and re.sub(r"[^a-z0-9]+", "", leaf.casefold()) == "id"
                )
                or (
                    has_exact_option_scope
                    and _is_idlike(leaf)
                    and re.sub(r"[^a-z0-9]+", "", leaf.casefold()) != "id"
                )
            )
            rebindable_option = bool(
                param.source_kind in {"api_option", "form_option"}
                and has_recorded_choice(param)
            ) or bool(
                param.source_kind == "page_enum"
                and (
                    (param.source or {}).get("enum_confirmed") is False
                    or not _incomplete_page_enum_is_executable(param)
                )
            ) or displaced_automatic_option
            if (
                param.locked
                or param.source_kind in {"dynamic_structure", "selected_option_field"}
                or _param_has_manual_contract(param)
                or _param_has_grounded_direct_input_contract(param)
                or (
                    param.source_kind == "previous_response"
                    and not has_recorded_choice(param)
                    and not hydrated_option
                    and not selected_entity_target
                )
                or (
                    param.source_kind in _OPTION_SOURCE_KINDS
                    and not rebindable_option
                )
                or _looks_pagination_field(param.key, param.path)
                or (
                    _looks_quantitative_option_target(param)
                    and not has_recorded_choice(param)
                )
                or (
                    not has_recorded_choice(param)
                    and (
                        _param_is_quantity_or_formula_leaf(param.key, param.path)
                        or _looks_unit_price_formula_leaf(param.key, param.path)
                        or _looks_display_echo_field(target, param)
                        or _looks_catalog_attribute_leaf(param.key, param.path)
                    )
                )
                or param.category == "system_const"
            ):
                continue
            value = str(param.value if param.value is not None else "").strip()
            if not value:
                continue
            target_text = " ".join((str(param.path or ""), str(param.key or ""), str(param.label or "")))
            target_tokens = _option_binding_tokens(target_text)
            target_families = _option_binding_semantic_families(target_text)
            page_contracts = page_evidence_for(target, param, value)
            matches: list[dict[str, Any]] = []
            for source in param_candidates:
                if source.get("entity_collection_source") is True and not selected_entity_target:
                    continue
                items = source["items"]
                source_url = str(source.get("source_url") or "")
                # Response rows commonly contain generic audit/status/name/id
                # columns. Those columns describe the returned entity, not
                # which form control owns the endpoint. Endpoint semantics or
                # an exact field-local candidate set may bridge ownership;
                # arbitrary row keys may not.
                source_text = source_url
                source_tokens = _option_binding_tokens(source_text)
                source_families = _option_binding_semantic_families(source_text)
                semantic_match = bool(
                    (target_tokens & source_tokens)
                    or (target_families & source_families)
                    or (
                        selected_entity_target
                        and source.get("entity_collection_source") is True
                    )
                )
                # Page evidence is a strong bridge when it describes this
                # source, but an unrelated open popup must not suppress a
                # valid semantic source (for example an approver directory
                # captured while the leave-type popup is still visible).
                source_page_contracts = [*page_contracts, None] if page_contracts else [None]
                for page_contract in source_page_contracts:
                    # Timing plus a matching scalar value does not identify an
                    # option owner. Require either field-local visible options
                    # or a semantic bridge to the candidate endpoint.
                    if page_contract is None and not semantic_match:
                        continue
                    if not source_is_grounded_for_target(source, target, page_contract, semantic_match):
                        continue
                    allow_single = bool(
                        source.get("explicit_option_source")
                        or (
                            value_owner_counts.get(value) == 1
                            and page_contract is not None
                        )
                    )
                    for contract in row_contracts(
                        items, value, page_contract, allow_single=allow_single,
                    ):
                        matches.append({**source, **contract})
            grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
            for match in matches:
                endpoint = _option_source_contract_endpoint(
                    str(match.get("source_url") or "")
                )
                selected_labels = tuple(sorted(
                    str(label)
                    for label, option_value in match["option_map"].items()
                    if str(option_value) == value
                ))
                fingerprint = (
                    endpoint,
                    match["value_key"], match["label_key"],
                    str(match.get("category_key") or ""),
                    str(match.get("category_value") or ""),
                    selected_labels,
                )
                def rank(item: dict[str, Any]) -> tuple[int, int, int, float]:
                    try:
                        sequence = float(item.get("sequence"))
                    except (TypeError, ValueError):
                        sequence = -1.0
                    return (
                        len(item.get("records") or []),
                        1 if item.get("explicit_option_source") else 0,
                        1 if item.get("source_step_id") else 0,
                        sequence,
                    )

                grouped.setdefault(fingerprint, []).append(match)
            unique: dict[tuple[Any, ...], dict[str, Any]] = {}
            for fingerprint, equivalent in grouped.items():
                owned = [item for item in equivalent if _source_owned_by_param(item, param)]
                owned_ids = {
                    str(item.get("source_request_id") or "") for item in owned
                    if item.get("source_request_id")
                }
                pool = owned if len(owned_ids) == 1 else equivalent
                selected = max(pool, key=rank)
                request_ids = {
                    str(item.get("source_request_id") or "") for item in equivalent
                    if item.get("source_request_id")
                }
                if len(owned_ids) != 1 and len(request_ids) > 1:
                    selected = {
                        **selected,
                        "source_request_id": "",
                        "source_step_id": "",
                    }
                unique[fingerprint] = selected
            if len(unique) != 1:
                continue
            match = next(iter(unique.values()))
            multi = bool(param.type in {"array", "list-enum"} and not re.search(r"\[\d+\]$", param.path or ""))
            _bind_option_source(
                spec,
                target_step_id=target.step_id,
                target_path=param.path,
                source_step_id=str(match.get("source_step_id") or ""),
                source_url=str(match.get("source_url") or ""),
                source_request_id=str(match.get("source_request_id") or ""),
                value_key=match["value_key"],
                label_key=match["label_key"],
                category_key=match.get("category_key"),
                category_value=match.get("category_value"),
                id_path=param.path,
                options=match["records"],
                option_map=match["option_map"],
                multi=multi,
                actor="recorder",
            )
            if selected_entity_target:
                selected_rows = [
                    item for item in match["items"]
                    if str(item.get(match["value_key"])) == value
                ]
                selector = _find_select_binding(target, param)
                if len(selected_rows) == 1 and selector is not None:
                    selected_row = selected_rows[0]
                    selector_group = _param_group_prefix(param.path)
                    projected_paths: set[str] = set()
                    for sibling in target.params or []:
                        if sibling is param or sibling.source_kind not in {
                            "api_option", "selected_option_field",
                        }:
                            continue
                        if _param_group_prefix(sibling.path) != selector_group:
                            continue
                        sibling_source = sibling.source or {}
                        selector_matches = bool(
                            sibling.source_kind == "selected_option_field"
                            and _strip_body_prefix(str(sibling_source.get("selector_path") or ""))
                            == _strip_body_prefix(param.path or "")
                        )
                        sibling_endpoint = _option_source_contract_endpoint(
                            str(sibling_source.get("source_url") or "")
                        )
                        selected_endpoint = _option_source_contract_endpoint(
                            str(match.get("source_url") or "")
                        )
                        same_source = bool(
                            (
                                sibling_source.get("source_request_id")
                                and sibling_source.get("source_request_id")
                                == match.get("source_request_id")
                            )
                            or (
                                sibling_endpoint
                                and selected_endpoint
                                and sibling_endpoint == selected_endpoint
                            )
                            or selector_matches
                        )
                        response_path = str(
                            sibling_source.get("response_path")
                            or sibling_source.get("value_key")
                            or ""
                        )
                        if not (
                            same_source
                            and response_path
                            and _recorded_scalar_values_match(
                                sibling.value,
                                _flow_path_lookup(selected_row, response_path),
                            )
                        ):
                            continue
                        selector.field_projections[sibling.path] = response_path
                        sibling.source_kind = "selected_option_field"
                        sibling.source = {
                            "kind": "selected_option_field",
                            "selector_path": param.path,
                            "selector_param": param.key,
                            "source_url": str(match.get("source_url") or ""),
                            "source_step_id": str(match.get("source_step_id") or ""),
                            "source_request_id": str(match.get("source_request_id") or ""),
                            "response_path": response_path,
                            "target_path": sibling.path,
                            **({
                                "allow_caller_override": True,
                            } if sibling_source.get("allow_caller_override") else {}),
                        }
                        projected_value = _flow_path_lookup(selected_row, response_path)
                        if isinstance(projected_value, str) or isinstance(sibling.value, str):
                            sibling.type = "string"
                            if isinstance(sibling.value, str):
                                sibling.wire_type = "string"
                        caller_override = _apply_selected_option_field_caller_ownership(sibling)
                        sibling.reason = (
                            f"该字段默认来自所选记录的 `{response_path}`，调用方可修改"
                            if caller_override
                            else f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                        )
                        projected_paths.add(sibling.path)
                    for sibling in target.params or []:
                        sibling_source = dict(sibling.source or {})
                        automatic_recorded_default = bool(
                            sibling.source_kind == "constant"
                            and str(sibling_source.get("kind") or "")
                            == "recorded_control_default"
                            and not _param_has_manual_contract(sibling)
                        )
                        if (
                            sibling is param
                            or _param_group_prefix(sibling.path) != selector_group
                            or sibling.path in projected_paths
                            or sibling.locked
                            or (
                                sibling.source_kind in {
                                    "user_input", "page_default", "constant", "system_time",
                                    "system_generated", "computed", "current_user",
                                    "dynamic_structure", "selected_option_field",
                                }
                                and not automatic_recorded_default
                            )
                            or _param_has_manual_contract(sibling)
                            or _param_has_editable_control_evidence(sibling)
                            or (
                                _looks_user_entered_business_field(
                                    sibling.key, sibling.path,
                                )
                                and not automatic_recorded_default
                            )
                            or _param_is_quantity_or_formula_leaf(sibling.key, sibling.path)
                        ):
                            continue
                        response_path = _best_option_projection_path(
                            selected_row, sibling.path, sibling.value,
                            allow_unique_value_fallback=(
                                int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2
                            ),
                        )
                        if not response_path:
                            continue
                        selector.field_projections[sibling.path] = response_path
                        sibling.source_kind = "selected_option_field"
                        sibling.source = {
                            "kind": "selected_option_field",
                            "selector_path": param.path,
                            "selector_param": param.key,
                            "source_url": str(match.get("source_url") or ""),
                            "source_step_id": str(match.get("source_step_id") or ""),
                            "source_request_id": str(match.get("source_request_id") or ""),
                            "response_path": response_path,
                            "target_path": sibling.path,
                        }
                        projected_value = _flow_path_lookup(selected_row, response_path)
                        if isinstance(projected_value, str) or isinstance(sibling.value, str):
                            sibling.type = "string"
                            if isinstance(sibling.value, str):
                                sibling.wire_type = "string"
                        caller_override = _apply_selected_option_field_caller_ownership(sibling)
                        sibling.reason = (
                            f"该字段默认来自所选记录的 `{response_path}`，调用方可修改"
                            if caller_override
                            else f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                        )
                        projected_paths.add(sibling.path)
                    if projected_paths:
                        target.selects = [
                            binding for binding in target.selects
                            if binding is selector
                            or str(binding.path or binding.id_path or "") not in projected_paths
                        ]
            repaired += 1

    repaired += _restore_executable_option_request_ids(spec)
    return repaired

_PENDING_FLOW_SPEC_HELPERS = {'_OPTION_SOURCE_KINDS': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_best_option_projection_path': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_bind_option_source': 'dano.execution.page.flow_materialization.field_contracts.option_sync', '_enum_label_value': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_find_select_binding': 'dano.execution.page.flow_materialization.field_contracts.option_sync', '_flow_path_lookup': 'dano.execution.page.flow_spec_core.normalization', '_incomplete_page_enum_is_executable': 'dano.execution.page.flow_release', '_looks_unit_price_formula_leaf': 'dano.execution.page.flow_materialization.field_contracts.computed', '_option_source_contract_endpoint': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_param_is_quantity_or_formula_leaf': 'dano.execution.page.flow_materialization.field_contracts.computed', '_read_is_business_entity_collection': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_read_is_option_source': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_read_transport_can_supply_options': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_recorded_scalar_values_match': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_refresh_param_enum_description': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
