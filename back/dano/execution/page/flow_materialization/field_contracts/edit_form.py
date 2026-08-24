"""Stage 5: edit hydration and readonly/display contracts."""
from __future__ import annotations

from typing import Any
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.flow_spec_core.normalization import _strip_body_prefix
from dano.execution.page.recording_facts import (
    _field_leaf_token,
    _page_enum_options_from_request_facts,
    _recording_evidence_matches_scope,
    _request_path,
)
from dano.execution.page.request_capture import (
    _is_idlike,
    _pick_label_key,
    as_list_payload,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_audit_system_leaf,
    _param_field_manually_edited,
    _param_control_is_readonly,
    _param_group_prefix,
    _param_has_manual_contract,
    _param_source_agent_classified,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _looks_row_identity_leaf,
    _param_is_document_record_identity,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_has_editable_control_evidence,
)


def _normalized_control_label(value: Any) -> str:
    return re.sub(
        r"[^0-9a-zA-Z\u4e00-\u9fff]+", "",
        re.sub(r"#\d+$", "", str(value or "").strip()),
    ).casefold()


def _option_labels(options: list[Any] | None) -> set[str]:
    labels: set[str] = set()
    for option in options or []:
        if isinstance(option, dict):
            raw = next(
                (
                    option.get(key) for key in ("label", "text", "name", "title", "value")
                    if option.get(key) not in (None, "")
                ),
                "",
            )
        elif isinstance(option, (list, tuple)):
            raw = option[0] if option else ""
        else:
            raw = option
        label = str(raw or "").strip()
        if label:
            # API choices with duplicate display names are rendered publicly
            # as ``name [wire-value]``. DOM snapshots still contain the raw
            # display name, so compare their candidate sets on that raw label.
            labels.add(re.sub(r"\s+\[[^\]]+\]$", "", label))
    return labels


def _same_control_scalar(left: Any, right: Any) -> bool:
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return False
    if left in (None, "") or right in (None, ""):
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip().casefold() == str(right).strip().casefold()


def _project_reconciled_control(
    step: FlowStep,
    param: ParamField,
    control: dict[str, Any],
    *,
    bind_raw: bool = False,
) -> None:
    """Attach one mechanically proven unbound control to its request field."""
    request_id = str((step.source_meta or {}).get("request_id") or "")
    wire_path = str(param.path or "")
    if not wire_path.startswith(("body.", "query.")):
        wire_path = f"{'query' if str(step.method or '').upper() in {'GET', 'HEAD'} else 'body'}.{wire_path}"
    if bind_raw:
        control.update({
            "binding_status": "bound",
            "binding_method": "field_contract_reconciliation",
            "request_id": request_id,
            "wire_path": wire_path,
            "binding_candidates": [{"request_id": request_id, "wire_path": wire_path}],
        })
    label = str(control.get("label") or control.get("field") or "").strip()
    if label and not _param_field_manually_edited(param, "label"):
        param.label = label
    kind = str(control.get("control_kind") or "unknown").lower()
    projected = {
        "kind": "page_control",
        "source": "recorder_dom",
        "control_kind": kind,
        "interacted": str(control.get("op") or "").lower() in {"fill", "select", "pick"},
        "request_path": param.path,
        "binding_status": "bound",
        "evidence_id": str(control.get("evidence_id") or ""),
        "occurrence_id": str(control.get("occurrence_id") or ""),
        "field_identity_id": str(control.get("field_identity_id") or ""),
        "field_aliases": list(control.get("field_aliases") or []),
        "required": control.get("required_observed") is True,
        "required_observed": control.get("required_observed"),
        "editable": bool(control.get("editable")),
        "disabled": bool(control.get("disabled")),
        "read_only": bool(control.get("read_only")),
        "page_id": str(control.get("page_id") or ""),
        "frame_id": str(control.get("frame_id") or ""),
        "surface": str(control.get("surface") or ""),
        "in_dialog": bool(control.get("in_dialog")),
        "form_root": str(control.get("form_root") or ""),
        "row_index": control.get("row_index"),
        "column_index": control.get("column_index"),
    }
    evidence_id = str(projected.get("evidence_id") or "")
    field_identity_id = str(projected.get("field_identity_id") or "")
    param.evidence = [
        item for item in (param.evidence or [])
        if not (
            isinstance(item, dict)
            and item.get("kind") == "page_control"
            and str(item.get("request_path") or "") == str(param.path or "")
            and (
                (evidence_id and str(item.get("evidence_id") or "") == evidence_id)
                or (
                    field_identity_id
                    and str(item.get("field_identity_id") or "") == field_identity_id
                )
            )
        )
    ]
    param.evidence.append(projected)
    if control.get("required_observed") is True:
        param.evidence.append({
            "kind": "page_required",
            "source": "recorder_dom",
            "request_path": param.path,
            "binding_status": "bound",
            "evidence_id": str(control.get("evidence_id") or ""),
        })
        param.required = True
        param.source = {**(param.source or {}), "required_state": "required"}
    elif control.get("required_observed") is False:
        param.required = False
        param.source = {**(param.source or {}), "required_state": "optional"}


def _restore_selected_option_projections(spec: FlowSpec) -> None:
    """Keep one chooser occurrence from becoming two editable inputs.

    A grounded select binding may project sibling fields from its selected API
    row. If an earlier ambiguous reconciliation attached the chooser's exact
    same DOM identity to a projected sibling, the duplicate control evidence
    is impossible: one visible control cannot edit two wire leaves. Restore the
    projection and discard only that duplicated occurrence. A distinct control
    identity is preserved as real caller-editable evidence.
    """
    for step in spec.steps or []:
        by_path = {
            _strip_body_prefix(param.path or ""): param
            for param in (step.params or [])
        }
        for binding in step.selects or []:
            selector_path = _strip_body_prefix(binding.path or binding.id_path or "")
            selector = by_path.get(selector_path)
            if selector is None:
                continue
            selector_ids = {
                str(item.get("field_identity_id") or "")
                for item in (selector.evidence or [])
                if isinstance(item, dict)
                and item.get("kind") == "page_control"
                and str(item.get("field_identity_id") or "")
            }
            selector_groups = {
                (str(item.get("form_root") or ""), item.get("row_index"))
                for item in (selector.evidence or [])
                if isinstance(item, dict) and item.get("kind") == "page_control"
            }
            for target_path, response_path in (binding.field_projections or {}).items():
                target = by_path.get(_strip_body_prefix(str(target_path or "")))
                if target is None or target is selector or _param_has_manual_contract(target):
                    continue
                target_controls = [
                    item for item in (target.evidence or [])
                    if isinstance(item, dict) and item.get("kind") == "page_control"
                ]
                target_ids = {
                    str(item.get("field_identity_id") or "")
                    for item in target_controls
                    if str(item.get("field_identity_id") or "")
                }
                target_groups = {
                    (str(item.get("form_root") or ""), item.get("row_index"))
                    for item in target_controls
                }
                distinct_local_control = bool(
                    target_ids
                    and not target_ids.issubset(selector_ids)
                    and (
                        not selector_groups
                        or bool(target_groups.intersection(selector_groups))
                    )
                )
                if distinct_local_control:
                    continue
                duplicated_evidence_ids = {
                    str(item.get("evidence_id") or "")
                    for item in target_controls
                    if not distinct_local_control
                }
                target.evidence = [
                    item for item in (target.evidence or [])
                    if not (
                        isinstance(item, dict)
                        and (
                            item.get("kind") == "page_control"
                            and not distinct_local_control
                            or item.get("kind") == "page_required"
                            and str(item.get("evidence_id") or "") in duplicated_evidence_ids
                        )
                    )
                ]
                target.category = "runtime_var"
                target.source_kind = "selected_option_field"
                target.source = {
                    "kind": "selected_option_field",
                    "selector_path": selector.path,
                    "selector_param": selector.key,
                    "source_url": str(binding.source_url or ""),
                    "source_request_id": str(binding.source_request_id or ""),
                    "response_path": str(response_path or ""),
                    "target_path": target.path,
                }
                target.exposed_to_user = False
                target.editable = False
                target.required = False
                target.need_human_confirm = False
                if (
                    target_controls
                    and not distinct_local_control
                    and _normalized_control_label(target.label)
                    == _normalized_control_label(selector.label)
                ):
                    target.label = target.key or str(target.path or "").split(".")[-1]
                target.reason = (
                    f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                )


def _reconcile_unbound_editable_controls(spec: FlowSpec) -> int:
    """Join aliasless controls by exact option sets or a unique scoped value.

    This is the deterministic bridge between capture and edit ownership. It
    never translates labels or relies on endpoint names: complete candidate
    sets win first and a unique same-row scalar is the value fallback.
    """
    if int((spec.meta or {}).get("stage_1_6_contract_version") or 0) < 2:
        return 0
    _restore_selected_option_projections(spec)
    from dano.execution.page.flow_materialization.field_contracts.computed import (
        _looks_percent_formula_leaf,
        _param_is_quantity_or_formula_leaf,
    )

    raw_controls = [
        item for item in (getattr(spec.request_facts, "field_evidence", []) or [])
        if isinstance(item, dict)
        and str(item.get("binding_status") or "") in {
            "bound", "unbound", "unresolved", "ambiguous",
        }
        and item.get("editable") is not False
        and item.get("disabled") is not True
        and str(item.get("control_kind") or "unknown").lower() != "table_column"
    ]
    if not raw_controls:
        return 0
    page_options = _page_enum_options_from_request_facts(spec.request_facts)
    catalog_contracts: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fact in spec.request_facts.requests or []:
        request_id = str(fact.request_id or "")
        analysis = (spec.request_facts.analysis or {}).get(request_id)
        role = str(analysis.role if analysis is not None else "")
        if role not in {"option", "read_option", "option_source", "explicit_read_option"}:
            continue
        rows = [
            dict(item) for item in (as_list_payload(fact.response_json) or [])
            if isinstance(item, dict)
        ]
        if len(rows) < 2:
            continue
        for value_key in {
            str(key) for row in rows for key in row if _is_idlike(str(key))
        }:
            label_keys = {
                _pick_label_key(row, value_key) for row in rows
                if value_key in row and _pick_label_key(row, value_key) != value_key
            }
            for label_key in label_keys:
                records = [
                    (str(row.get(label_key) or "").strip(), row.get(value_key))
                    for row in rows
                    if row.get(value_key) not in (None, "")
                ]
                if (
                    len(records) < 2
                    or any(not label for label, _value in records)
                    or len({str(value) for _label, value in records}) != len(records)
                ):
                    continue
                labels = {label for label, _value in records}
                endpoint = _request_path({"url": fact.url or fact.path})
                fingerprint = (endpoint, value_key, label_key, tuple(sorted(labels)))
                catalog_contracts[fingerprint] = {
                    "endpoint": endpoint,
                    "value_key": value_key,
                    "label_key": label_key,
                    "labels": labels,
                    "records": records,
                    "fact": fact,
                }
    repaired = 0
    for step in spec.steps or []:
        if str(step.method or "GET").upper() not in {"POST", "PUT", "PATCH"}:
            continue
        request_id = str((step.source_meta or {}).get("request_id") or "")
        controls = [
            item for item in raw_controls
            if item.get("in_dialog") is not False
            and _recording_evidence_matches_scope(step.source_meta or {}, item)
            and (
                str(item.get("binding_status") or "") != "bound"
                or str(item.get("request_id") or "") == request_id
            )
        ]
        if not controls:
            continue
        # Repeated snapshots of one visible label describe one control
        # contract. Prefer the unlocked observation over submit-time locks.
        by_label: dict[str, list[dict[str, Any]]] = {}
        for control in controls:
            label = _normalized_control_label(control.get("label") or control.get("field"))
            if label:
                by_label.setdefault(label, []).append(control)
        representatives = {
            label: max(
                items,
                key=lambda item: (
                    int(item.get("editable") is True and item.get("disabled") is not True),
                    int(item.get("required_observed") is True),
                    float(item.get("observed_at") or 0),
                ),
            )
            for label, items in by_label.items()
        }

        def executable_option_labels(param: ParamField) -> set[str]:
            labels = _option_labels(param.enum_options)
            if labels:
                return labels
            param_path = _strip_body_prefix(param.path or "")
            binding = next((
                item for item in (step.selects or [])
                if _strip_body_prefix(item.path or item.id_path or "") == param_path
            ), None)
            if binding is None:
                return set()
            return _option_labels(binding.options) or {
                str(label) for label in (binding.option_map or {}) if str(label)
            }

        option_params = [
            param for param in (step.params or [])
            if not param.locked
            and not _param_has_manual_contract(param)
            and param.source_kind != "selected_option_field"
            and (
                param.source_kind in {"api_option", "form_option", "page_enum", "static_enum", "manual_enum"}
                or isinstance((param.source or {}).get("option_source"), dict)
                or (
                    param.type in {"enum", "list-enum"}
                    and bool(param.enum_options)
                )
            )
            and executable_option_labels(param)
            and not _param_has_editable_control_evidence(param)
        ]
        used_labels: set[str] = set()
        used_params: set[int] = set()
        for label, control in representatives.items():
            if str(control.get("control_kind") or "").lower() not in {"select", "combobox", "radio"}:
                continue
            enum_sets = []
            for raw_key, raw in page_options.items():
                if not isinstance(raw, dict):
                    continue
                raw_names = {
                    _normalized_control_label(raw_key),
                    _normalized_control_label(raw.get("field_key")),
                    _normalized_control_label(raw.get("label")),
                }
                if label not in raw_names:
                    continue
                labels = _option_labels(list(raw.get("options") or raw.get("values") or []))
                if len(labels) >= 2:
                    enum_sets.append(labels)
            enum_sets = list({tuple(sorted(values)): values for values in enum_sets}.values())
            if len(enum_sets) != 1:
                continue
            matches = [
                param for param in option_params
                if id(param) not in used_params
                and executable_option_labels(param) == enum_sets[0]
            ]
            if len(matches) != 1:
                continue
            param = matches[0]
            _project_reconciled_control(step, param, control)
            used_labels.add(label)
            used_params.add(id(param))
            repaired += 1

        # A page control can identify a still-untyped request field through a
        # complete option candidate set plus the selected wire value. This is
        # stronger than label similarity and does not depend on endpoint names.
        for label, control in representatives.items():
            if label in used_labels or str(control.get("control_kind") or "").lower() not in {
                "select", "combobox", "radio",
            }:
                continue
            control_sets = []
            for raw_key, raw in page_options.items():
                if not isinstance(raw, dict):
                    continue
                raw_names = {
                    _normalized_control_label(raw_key),
                    _normalized_control_label(raw.get("field_key")),
                    _normalized_control_label(raw.get("label")),
                }
                if label not in raw_names:
                    continue
                labels = _option_labels(list(raw.get("options") or raw.get("values") or []))
                if len(labels) >= 2:
                    control_sets.append(labels)
            control_sets = list({tuple(sorted(values)): values for values in control_sets}.values())
            if len(control_sets) != 1:
                continue
            contracts = [
                contract for contract in catalog_contracts.values()
                if contract["labels"] == control_sets[0]
                and _recording_evidence_matches_scope(
                    step.source_meta or {}, contract["fact"].model_dump(exclude_none=True),
                )
            ]
            candidates: dict[int, ParamField] = {}
            matched_contracts: dict[int, set[tuple[str, str, str]]] = {}
            for param in step.params or []:
                row_index = control.get("row_index")
                param_indexes = [
                    int(part) for part in re.findall(r"\[(\d+)\]", str(param.path or ""))
                ]
                if (
                    param.locked
                    or _param_has_manual_contract(param)
                    or _param_has_editable_control_evidence(param)
                    or param.source_kind == "selected_option_field"
                    or _param_is_document_record_identity(param)
                    or _looks_row_identity_leaf(param.key, param.path)
                    or _looks_audit_system_leaf(param.key, param.path)
                    or _param_is_quantity_or_formula_leaf(param.key, param.path)
                    or (row_index is None and bool(param_indexes))
                    or (
                        row_index is not None
                        and (not param_indexes or int(row_index) not in param_indexes)
                    )
                ):
                    continue
                for contract in contracts:
                    matching = [
                        value for _option_label, value in contract["records"]
                        if _same_control_scalar(value, param.value)
                    ]
                    if len(matching) != 1:
                        continue
                    candidates[id(param)] = param
                    matched_contracts.setdefault(id(param), set()).add((
                        contract["endpoint"], contract["value_key"], contract["label_key"],
                    ))
            candidates = {
                param_id: param for param_id, param in candidates.items()
                if len(matched_contracts.get(param_id, set())) == 1
            }
            if len(candidates) != 1:
                continue
            param = next(iter(candidates.values()))
            _project_reconciled_control(step, param, control)
            used_labels.add(label)
            used_params.add(id(param))
            repaired += 1

        # Unit symbols are language-independent field evidence. They recover
        # aliasless percentage controls inside one repeating row without a
        # page-specific label dictionary (for example `税率（%）` → `taxPercent`).
        for label, control in representatives.items():
            raw_label = str(control.get("label") or control.get("field") or "")
            if (
                label in used_labels
                or "%" not in raw_label.replace("％", "%")
                or str(control.get("binding_status") or "") == "bound"
                or str(control.get("control_kind") or "").lower()
                in {"select", "combobox", "radio", "table_column"}
            ):
                continue
            row_index = control.get("row_index")
            candidates: list[ParamField] = []
            for param in step.params or []:
                indexes = [
                    int(part) for part in re.findall(r"\[(\d+)\]", str(param.path or ""))
                ]
                if (
                    param.locked
                    or _param_has_manual_contract(param)
                    or _param_has_editable_control_evidence(param)
                    or not _looks_percent_formula_leaf(param.key, param.path)
                    or (row_index is None and bool(indexes))
                    or (
                        row_index is not None
                        and (not indexes or int(row_index) not in indexes)
                    )
                ):
                    continue
                candidates.append(param)
            if len(candidates) != 1:
                continue
            _project_reconciled_control(step, candidates[0], control)
            used_labels.add(label)
            used_params.add(id(candidates[0]))
            repaired += 1

        for control in controls:
            if str(control.get("binding_status") or "") == "bound":
                continue
            if str(control.get("control_kind") or "").lower() in {"select", "combobox", "radio"}:
                continue
            if any(str(alias or "").strip() for alias in (control.get("field_aliases") or [])):
                # A structural alias is stronger than scalar equality. If it
                # remains request-ambiguous, do not move the control onto a
                # different same-valued field (for example remark="1" onto
                # creator="1").
                continue
            value = control.get("value")
            if value in (None, ""):
                continue
            row_index = control.get("row_index")
            candidates: list[ParamField] = []
            for param in step.params or []:
                if (
                    param.locked
                    or _param_has_manual_contract(param)
                    or _param_has_editable_control_evidence(param)
                    or param.source_kind in {"computed", "current_user", "system_time", "system_generated", "constant"}
                    or _param_is_quantity_or_formula_leaf(param.key, param.path)
                    or not _same_control_scalar(value, param.value)
                ):
                    continue
                indexes = [int(part) for part in re.findall(r"\[(\d+)\]", str(param.path or ""))]
                if row_index is not None and (not indexes or int(row_index) not in indexes):
                    continue
                candidates.append(param)
            if len(candidates) != 1:
                continue
            _project_reconciled_control(step, candidates[0], control)
            repaired += 1

        # Final structural tier: after stronger alias/candidate/value matches
        # anchor a dialog (or one repeating row) to this exact write request,
        # a single remaining chooser and a single remaining enum field form an
        # unambiguous pair. This is elimination within one form group, not a
        # page-specific label dictionary. Multiple unresolved pairs remain
        # untouched because JSON key order is not reliable visual position.
        grouped_controls: dict[tuple[str, Any], list[dict[str, Any]]] = {}
        for control in controls:
            if str(control.get("control_kind") or "").lower() not in {
                "select", "combobox", "radio",
            }:
                continue
            group = (str(control.get("form_root") or ""), control.get("row_index"))
            grouped_controls.setdefault(group, []).append(control)
        bound_choice_identities = {
            str(control.get("field_identity_id") or control.get("evidence_id") or "")
            for control in controls
            if str(control.get("control_kind") or "").lower() in {
                "select", "combobox", "radio",
            }
            and str(control.get("binding_status") or "") == "bound"
            and str(control.get("request_id") or "") == request_id
        }
        for (form_root, row_index), group_controls in grouped_controls.items():
            anchored = {
                str(control.get("field_identity_id") or control.get("evidence_id") or "")
                for control in controls
                if str(control.get("form_root") or "") == form_root
                and control.get("row_index") == row_index
                and str(control.get("binding_status") or "") == "bound"
                and str(control.get("request_id") or "") == request_id
            }
            if len({item for item in anchored if item}) < 2:
                continue
            remaining_controls: dict[str, dict[str, Any]] = {}
            for control in group_controls:
                label = _normalized_control_label(
                    control.get("label") or control.get("field")
                )
                if (
                    not label
                    or label in used_labels
                    or str(control.get("binding_status") or "") == "bound"
                    or control.get("editable") is False
                    or control.get("disabled") is True
                ):
                    continue
                identity = str(
                    control.get("field_identity_id")
                    or control.get("evidence_id")
                    or label
                )
                if identity in bound_choice_identities:
                    continue
                previous = remaining_controls.get(identity)
                if previous is None or (
                    int(control.get("read_only") is not True),
                    float(control.get("observed_at") or 0),
                ) > (
                    int(previous.get("read_only") is not True),
                    float(previous.get("observed_at") or 0),
                ):
                    remaining_controls[identity] = control
            if len(remaining_controls) != 1:
                continue
            remaining_params: list[ParamField] = []
            for param in step.params or []:
                if (
                    id(param) in used_params
                    or param.locked
                    or _param_has_manual_contract(param)
                    or _param_has_editable_control_evidence(param)
                    or param.source_kind == "selected_option_field"
                    or _param_is_document_record_identity(param)
                    or _looks_row_identity_leaf(param.key, param.path)
                    or _looks_audit_system_leaf(param.key, param.path)
                    or _looks_display_echo_field(step, param)
                    or _param_is_quantity_or_formula_leaf(param.key, param.path)
                    or _field_leaf_token(param.key, param.path) in {"status", "state"}
                    or not (
                        param.type in {"enum", "list-enum"}
                        or bool(param.enum_options)
                        or isinstance((param.source or {}).get("option_source"), dict)
                    )
                ):
                    continue
                indexes = [
                    int(part) for part in re.findall(r"\[(\d+)\]", str(param.path or ""))
                ]
                if row_index is None and indexes:
                    continue
                if row_index is not None and (
                    not indexes or int(row_index) not in indexes
                ):
                    continue
                remaining_params.append(param)
            if len(remaining_params) != 1:
                continue
            control = next(iter(remaining_controls.values()))
            param = remaining_params[0]
            _project_reconciled_control(step, param, control, bind_raw=True)
            used_labels.add(_normalized_control_label(
                control.get("label") or control.get("field")
            ))
            used_params.add(id(param))
            repaired += 1
    return repaired


def _step_is_record_edit_form(step: FlowStep) -> bool:
    params = list(step.params or [])
    hydrated = [
        param for param in params
        if param.source_kind == "previous_response"
        and not _param_is_document_record_identity(param)
        # A selectable value list supplies candidates, not an existing
        # business record. Several dropdown APIs on a create form must not
        # make that form look like record hydration.
        and not isinstance((param.source or {}).get("option_source"), dict)
    ]
    if len(hydrated) >= 3:
        return True
    body_fields = [
        param for param in params
        if not str(param.path or "").startswith("query.")
        and not _param_is_document_record_identity(param)
    ]
    dialog_owned = any(_param_has_command_local_control(step, param) for param in body_fields)
    return len(hydrated) >= 2 and (len(body_fields) >= 2 or dialog_owned)


def _looks_catalog_attribute_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    if leaf.endswith("id") or leaf in {"id", "ids"}:
        return False
    return any(leaf.endswith(token) for token in (
        "name", "title", "label", "barcode", "unitname", "stock", "stockcount",
        "spec", "image", "img",
    ))


def _looks_display_echo_field(step: FlowStep, param: ParamField) -> bool:
    leaf = _field_leaf_token(param.key, param.path)
    stem = ""
    for suffix in ("name", "title", "label", "text"):
        if leaf.endswith(suffix) and len(leaf) > len(suffix):
            stem = leaf[: -len(suffix)]
            break
    if not stem:
        return False
    group = _param_group_prefix(param.path)
    for other in step.params or []:
        if other is param or _param_group_prefix(other.path) != group:
            continue
        other_leaf = _field_leaf_token(other.key, other.path)
        if other_leaf in {stem, f"{stem}id", f"{stem}ids"}:
            return True
    return False


def _mark_system_hydrated_field(param: ParamField, reason: str) -> None:
    param.category = "runtime_var"
    param.exposed_to_user = False
    param.editable = False
    param.required = False
    param.need_human_confirm = False
    if param.source_kind == "previous_response":
        param.source = {**(param.source or {}), "allow_caller_override": False, "required_state": "optional"}
        param.reason = reason
        return
    if param.source_kind in {"unknown", "user_input", "page_default"}:
        param.source_kind = "previous_response" if (param.source or {}).get("link_id") else param.source_kind
        param.source = {**(param.source or {}), "allow_caller_override": False, "required_state": "optional"}
        param.reason = reason


def _editable_required_state(param: ParamField) -> str:
    has_required_evidence = any(
        isinstance(item, dict)
        and item.get("kind") in {
            "page_control", "page_required", "successful_omit_optional",
        }
        for item in (param.evidence or [])
    )
    if not has_required_evidence:
        current = str((param.source or {}).get("required_state") or "")
        if current in {"required", "optional", "unknown"}:
            return current
        return "required" if param.required else "unknown"
    if any(
        isinstance(item, dict)
        and item.get("kind") == "page_required"
        and str(item.get("binding_status") or "bound") in {"bound", "parallel_contract"}
        for item in (param.evidence or [])
    ):
        return "required"
    observed = {
        item.get("required_observed")
        for item in (param.evidence or [])
        if isinstance(item, dict)
        and item.get("kind") == "page_control"
        and isinstance(item.get("required_observed"), bool)
    }
    if observed == {False}:
        return "optional"
    if any(
        isinstance(item, dict)
        and item.get("kind") == "successful_omit_optional"
        for item in (param.evidence or [])
    ):
        return "optional"
    return "unknown"


def _apply_edit_form_field_contracts(spec: FlowSpec) -> None:
    """Keep edit-form identity/audit/display echoes system-owned.

    Hydration makes most write leaves caller-overridable. The document id used
    to load the record, audit timestamps, and label echoes of a chosen *Id stay
    on the system side even when their values came from the detail GET.
    """
    strict_edit_evidence = int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2
    for step in spec.steps or []:
        if not _step_is_record_edit_form(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param) or param.source_kind == "computed":
                continue
            if _param_source_agent_classified(param):
                # Pi already compiled this exact field's semantic origin and
                # caller ownership. Name hints must not reclassify it later.
                continue
            has_editable_control = _param_has_editable_control_evidence(param)
            if _param_is_document_record_identity(param) or _looks_row_identity_leaf(param.key, param.path):
                _mark_system_hydrated_field(
                    param,
                    "该字段是记录或行项目标识，由详情接口回填，不作为调用方输入",
                )
                continue
            if (
                _looks_audit_system_leaf(param.key, param.path)
                and not has_editable_control
                and not _param_has_command_local_control(step, param)
            ):
                _mark_system_hydrated_field(
                    param,
                    "该字段是审计/系统时间或创建人痕迹，由详情接口回填，不作为调用方输入",
                )
                continue
            if (
                _field_leaf_token(param.key, param.path) in {"status", "state"}
                and not has_editable_control
                and not _param_has_command_local_control(step, param)
            ):
                _mark_system_hydrated_field(
                    param,
                    "该字段是单据状态回写，编辑提交随详情带出，不是列表筛选或行级命令",
                )
                continue
            if (
                _looks_display_echo_field(step, param)
                and not has_editable_control
                and not _param_has_command_local_control(step, param)
            ):
                _mark_system_hydrated_field(
                    param,
                    "该字段是选项显示名回写，随所选标识自动带出，不作为调用方输入",
                )
                continue
            if (
                param.source_kind == "previous_response"
                and param.value in (None, "")
                and not _param_has_command_local_control(step, param)
                and not has_editable_control
            ):
                _mark_system_hydrated_field(
                    param,
                    "该字段在详情与提交中均为空，随请求携带，不作为调用方输入",
                )
                continue
            if (
                param.source_kind == "previous_response"
                and not _param_control_is_readonly(param)
                and (
                    not _looks_audit_system_leaf(param.key, param.path)
                    or has_editable_control
                )
            ):
                hydrated_option = (
                    (param.source or {}).get("option_source")
                    if isinstance((param.source or {}).get("option_source"), dict)
                    else {}
                )
                executable_hydrated_option = bool(
                    hydrated_option.get("source_url")
                    and hydrated_option.get("value_key")
                    and hydrated_option.get("label_key")
                )
                if (
                    strict_edit_evidence
                    and not has_editable_control
                    and not executable_hydrated_option
                ):
                    _mark_system_hydrated_field(
                        param,
                        "该字段来自详情响应，但没有可编辑控件证据，保留为上游回填字段",
                    )
                    continue
                param.category = "user_param"
                param.exposed_to_user = True
                param.editable = True
                if strict_edit_evidence:
                    required_state = _editable_required_state(param)
                    param.required = required_state == "required"
                    param.source = {
                        **(param.source or {}),
                        "allow_caller_override": True,
                        "required_state": required_state,
                    }
                else:
                    # Frozen pre-tristate drafts did not serialize an inferred
                    # requiredness for every hydrated editable field.
                    param.source = {
                        **(param.source or {}),
                        "allow_caller_override": True,
                    }
                if "可修改" not in (param.reason or ""):
                    param.reason = (
                        f"{param.reason}；调用方仍可修改该字段，显式输入优先于上游默认值"
                        if param.reason else
                        "编辑场景默认来自上游详情；调用方仍可修改该字段，显式输入优先于上游默认值"
                    )


def _repair_readonly_control_defaults(spec: FlowSpec) -> int:
    """Bind an aliasless locked control only to one stable write-wire field.

    A disabled value can legitimately appear in several save/submit requests.
    Requiring one request would misclassify it as caller input, while matching
    by value alone could bind unrelated fields.  Accept it only when every
    scoped occurrence of that scalar has the same canonical wire path.
    """
    repaired = 0

    def same_scalar(left: Any, right: Any) -> bool:
        if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            return False
        return str(left).strip().casefold() == str(right).strip().casefold()

    evidence_items = [
        item for item in (getattr(spec.request_facts, "field_evidence", []) or [])
        if isinstance(item, dict)
        and item.get("value") not in (None, "")
        and item.get("editable") is False
        and (
            item.get("disabled") is True
            or (
                item.get("read_only") is True
                and str(item.get("control_kind") or "").lower()
                not in {"select", "combobox"}
            )
        )
    ]
    for evidence in evidence_items:
        candidates: list[tuple[FlowStep, ParamField, str]] = []
        for step in spec.steps or []:
            if not _is_write_step(step) or not _recording_evidence_matches_scope(
                step.source_meta or {}, evidence,
            ):
                continue
            for param in step.params or []:
                if not same_scalar(param.value, evidence.get("value")):
                    continue
                candidates.append((
                    step,
                    param,
                    _strip_body_prefix(str(param.path or param.key or "")),
                ))
        wire_paths = {path for _step, _param, path in candidates if path}
        if len(wire_paths) != 1 or not candidates:
            continue
        wire_path = next(iter(wire_paths))
        for step, param, _path in candidates:
            if (
                param.locked
                or param.source_kind in {"computed", "selected_option_field"}
                or (
                    param.source_kind == "previous_response"
                    and bool((param.source or {}).get("link_id"))
                )
                or _param_has_manual_contract(param)
                or _param_source_agent_classified(param)
                or _param_has_editable_control_evidence(param)
            ):
                continue
            param.category = "system_const"
            param.source_kind = "constant"
            param.source = {
                "kind": "recorded_control_default",
                "path": param.path,
                "wire_path": wire_path,
                "evidence_id": str(evidence.get("evidence_id") or ""),
            }
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            param.need_human_confirm = False
            param.reason = "页面证据证明该控件不可编辑；录制请求在同一 wire 字段使用其默认值"
            step.sample_inputs.pop(param.key, None)
            repaired += 1
    return repaired

_PENDING_FLOW_SPEC_HELPERS = {'_is_write_step': 'dano.execution.page.capability_kinds', '_param_has_command_local_control': 'dano.execution.page.flow_materialization.field_contracts.row_command', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
