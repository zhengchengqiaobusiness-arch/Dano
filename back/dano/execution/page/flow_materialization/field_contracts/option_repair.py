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
from dano.execution.page.flow_materialization.field_contracts.computed import (
    _looks_unit_price_formula_leaf,
    _param_is_quantity_or_formula_leaf,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_user_entered_business_field,
    _param_has_grounded_direct_input_contract,
    _param_has_manual_contract,
    _screenshot_control_evidence,
    _screenshot_control_supports_axis,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
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
        "person": r"(?:user|users|assignee|approver|reviewer|auditor|employee|member|person|people|审批|审核|人员|用户|负责人)",
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


def _repair_structural_option_bindings(spec: FlowSpec) -> int:
    """Recover grounded enum/reference bindings, including captured-only reads.

    Candidate selection is evidence based: an exact recorded wire value, a real
    ID/display row contract, and either control ownership, a shared semantic
    family, or an exact field token are required. Shared dictionary endpoints
    are narrowed only when the page's visible labels identify one category.
    Repeated captures of the same endpoint/contract are one source, not an
    ambiguity.
    """
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
            in {"recorder_dom", "page", "page_snapshot", "page_control"}
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
                normalized(str(name).split(":", 1)[-1])
                for name in (raw_key, raw.get("field_key"), *(raw.get("field_aliases") or []))
                if normalized(str(name).split(":", 1)[-1])
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
        return semantic_matches or fallback_matches

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
            label_keys = {
                _pick_label_key(item, value_key)
                for item in matching_items
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
                            if len(subset) >= 2:
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
                    records: list[dict[str, Any]] = []
                    option_map: dict[str, Any] = {}
                    seen_values: set[str] = set()
                    valid = True
                    for item in subset:
                        label = str(item.get(label_key) or "").strip()
                        raw_value = item.get(value_key)
                        value_sig = str(raw_value)
                        if not label or raw_value in (None, "") or label in option_map or value_sig in seen_values:
                            valid = False
                            break
                        seen_values.add(value_sig)
                        option_map[label] = raw_value
                        records.append({"label": label, "value": raw_value})
                    if not valid or len(records) < (1 if allow_single else 2):
                        continue
                    if page_contract:
                        record_labels = set(option_map)
                        required_overlap = min(2, len(visible_labels))
                        if len(record_labels & visible_labels) < required_overlap:
                            continue
                        if selected_label and (
                            selected_label not in option_map
                            or str(option_map[selected_label]) != value
                        ):
                            continue
                    contracts.append({
                        "value_key": value_key,
                        "label_key": label_key,
                        "category_key": category_key,
                        "category_value": category_value,
                        "records": records,
                        "option_map": option_map,
                    })
        if page_contract:
            exact_label_contracts = [
                contract for contract in contracts
                if set(contract["option_map"]) == visible_labels
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
        if semantic_match:
            return True
        source_tx = str(source.get("trigger_transaction_id") or "")
        source_action = str(source.get("trigger_action_id") or "")
        target_meta = target.source_meta or {}
        target_tx = str(target_meta.get("trigger_transaction_id") or "")
        target_action = str(target_meta.get("trigger_action_id") or "")
        source_page = str(source.get("page_id") or "")
        source_frame = str(source.get("frame_id") or "")
        target_page = str(target_meta.get("page_id") or "")
        target_frame = str(target_meta.get("frame_id") or "")
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
        for param in target.params or []:
            selected_entity_target = bool(
                str((param.source or {}).get("kind") or "") == "selected_entity_id"
                and re.sub(
                    r"[^a-z0-9]+", "",
                    str(param.path or param.key).split(".")[-1].casefold(),
                ) == "id"
            )
            rebindable_option = bool(
                param.source_kind == "api_option" and has_screenshot_choice(param)
            ) or bool(
                param.source_kind == "page_enum"
                and (
                    (param.source or {}).get("enum_confirmed") is False
                    or not _incomplete_page_enum_is_executable(param)
                )
            )
            if (
                param.locked
                or param.source_kind in {"dynamic_structure", "selected_option_field"}
                or _param_has_manual_contract(param)
                or _param_has_grounded_direct_input_contract(param)
                or (
                    param.source_kind == "previous_response"
                    and isinstance((param.source or {}).get("option_source"), dict)
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
            for source in candidates:
                if source.get("entity_collection_source") is True and not selected_entity_target:
                    continue
                items = source["items"]
                source_url = str(source.get("source_url") or "")
                source_text = " ".join([
                    source_url,
                    *[str(key) for item in items[:3] for key in item.keys()],
                ])
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
            unique: dict[tuple[Any, ...], dict[str, Any]] = {}
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
                previous = unique.get(fingerprint)

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

                if previous is None or rank(match) > rank(previous):
                    unique[fingerprint] = match
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
                    projected_paths: set[str] = set()
                    for sibling in target.params or []:
                        if sibling is param or sibling.source_kind != "api_option":
                            continue
                        sibling_source = sibling.source or {}
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
                        )
                        response_path = str(sibling_source.get("value_key") or "")
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
                        sibling.category = "runtime_var"
                        sibling.source_kind = "selected_option_field"
                        sibling.source = {
                            "kind": "selected_option_field",
                            "selector_path": param.path,
                            "selector_param": param.key,
                            "source_url": str(match.get("source_url") or ""),
                            "response_path": response_path,
                            "target_path": sibling.path,
                        }
                        sibling.exposed_to_user = False
                        sibling.editable = False
                        sibling.required = False
                        sibling.need_human_confirm = False
                        sibling.reason = (
                            f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                        )
                        projected_paths.add(sibling.path)
                    for sibling in target.params or []:
                        if (
                            sibling is param
                            or sibling.path in projected_paths
                            or sibling.locked
                            or sibling.source_kind in {
                                "user_input", "page_default", "constant", "system_time",
                                "system_generated", "computed", "current_user",
                                "dynamic_structure", "selected_option_field",
                            }
                            or _param_has_editable_control_evidence(sibling)
                            or _looks_user_entered_business_field(
                                sibling.key, sibling.path,
                            )
                            or _param_is_quantity_or_formula_leaf(sibling.key, sibling.path)
                        ):
                            continue
                        response_path = _best_option_projection_path(
                            selected_row, sibling.path, sibling.value,
                        )
                        if not response_path:
                            continue
                        selector.field_projections[sibling.path] = response_path
                        sibling.category = "runtime_var"
                        sibling.source_kind = "selected_option_field"
                        sibling.source = {
                            "kind": "selected_option_field",
                            "selector_path": param.path,
                            "selector_param": param.key,
                            "source_url": str(match.get("source_url") or ""),
                            "response_path": response_path,
                            "target_path": sibling.path,
                        }
                        sibling.exposed_to_user = False
                        sibling.editable = False
                        sibling.required = False
                        sibling.need_human_confirm = False
                        sibling.reason = (
                            f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                        )
                        projected_paths.add(sibling.path)
                    if projected_paths:
                        target.selects = [
                            binding for binding in target.selects
                            if binding is selector
                            or str(binding.path or binding.id_path or "") not in projected_paths
                        ]
            repaired += 1

    return repaired


_PENDING_FLOW_SPEC_HELPERS = ('_OPTION_SOURCE_KINDS', '_best_option_projection_path', '_bind_option_source', '_enum_label_value', '_find_select_binding', '_flow_path_lookup', '_incomplete_page_enum_is_executable', '_option_source_contract_endpoint', '_read_is_business_entity_collection', '_read_is_option_source', '_read_transport_can_supply_options', '_recorded_scalar_values_match', '_refresh_param_enum_description', '_strip_body_prefix',)


def _bind_flow_spec_helpers() -> None:
    import dano.execution.page.flow_spec as _flow_spec
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
