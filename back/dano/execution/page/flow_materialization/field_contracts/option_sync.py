"""Stage 5: option source sync, page-enum grounding, and select hydrate."""
from __future__ import annotations

from typing import Any
import copy
import json
import re
from urllib.parse import urlparse
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFacts,
    SelectBinding,
)
from dano.execution.page.request_capture import (
    _is_idlike,
    _pick_label_key,
    as_list_payload,
    looks_internal_param_name,
)
from dano.execution.page.recording_facts import (
    _looks_pagination_field,
    _page_enum_options_from_request_facts,
    _recording_evidence_matches_request,
    _recording_evidence_matches_scope,
    _request_path,
    _request_sequence_value,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _param_axis_manually_edited,
    _param_field_manually_edited,
    _param_has_manual_contract,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _apply_selected_option_field_caller_ownership,
)
from dano.execution.page.flow_materialization.request_steps import (
    _step_sequence,
)
from dano.execution.page.request_identity import (
    normalized_request_path,
    unique_request_identity_match,
)


def _ground_saved_page_enums(spec: FlowSpec) -> bool:
    """Recover enum contracts from immutable DOM evidence.

    Older or partially inferred specs can retain RequestFacts.option_sources
    while missing the SelectBinding that projects those facts to a request
    field. Re-running optimize/sync must be able to repair that state without
    another recording. A binding is created only for a unique semantic match;
    a selected wire value is supporting evidence, never enough on its own.
    """
    page_options = _page_enum_options_from_request_facts(spec.request_facts)
    if not page_options:
        return False
    changed = False

    def norm(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()

    def visible_field_name(value: Any) -> str:
        """Remove only recorder-added duplicate-label ordinals.

        ``#2`` is collection identity, not part of the visible field name.
        The suffix is ignored only while joining a page enum to an already
        bound control; wire identity still comes from that bound evidence.
        """
        return re.sub(r"#\d+$", "", str(value or "").strip())

    def wire_identity(value: Any) -> str:
        path = str(value or "").strip().removeprefix("request.")
        return path.removeprefix("body.").removeprefix("query.")

    def grounded_targets(raw: dict) -> list[dict[str, str]]:
        """Resolve dictionary evidence through request, control, and scope identity."""
        aliases = {
            norm(value) for value in (raw.get("field_aliases") or [])
            if norm(value)
        }
        field_name = norm(visible_field_name(raw.get("field_key")))
        if not aliases and not field_name:
            return []
        observations = [
            item for item in (raw.get("request_value_observations") or [])
            if isinstance(item, dict)
        ]
        option_pairs = [
            pair for option in (raw.get("options") or raw.get("values") or [])
            if (pair := _enum_label_value(option)) is not None
        ]
        direct: list[dict[str, str]] = []
        scoped: dict[str, list[dict[str, str]]] = {}
        for step in spec.steps:
            request_id = str((step.source_meta or {}).get("request_id") or "")
            scoped_evidence = [
                item for item in (getattr(spec.request_facts, "field_evidence", []) or [])
                if isinstance(item, dict)
                and field_name in {
                    norm(item.get("field")), norm(item.get("label")),
                }
                and _recording_evidence_matches_scope(step.source_meta or {}, item)
            ]
            matching_controls = [
                item for item in scoped_evidence
                if str(item.get("control_kind") or "").lower() == "select"
            ]
            for param in step.params or []:
                param_names = {
                    norm(param.key), norm(param.label), norm(param.path),
                    norm(wire_identity(param.path).split(".")[-1]),
                }
                target = {
                    "step_id": step.step_id,
                    "request_id": request_id,
                    "wire_path": param.path,
                }
                exact_controls = [
                    item for item in matching_controls
                    if request_id == str(item.get("request_id") or "")
                    and wire_identity(param.path) == wire_identity(item.get("wire_path"))
                ]
                if aliases:
                    if not aliases.intersection(param_names):
                        continue
                elif exact_controls:
                    # A duplicate visible label is safe only after the field
                    # evidence has already resolved it to one request path.
                    direct.append(target)
                    continue
                else:
                    continue
                if any(
                    request_id == str(item.get("request_id") or "")
                    and wire_identity(param.path) == wire_identity(item.get("wire_path"))
                    for item in observations
                ):
                    direct.append(target)
                    continue
                mapped_labels = {
                    str(label) for label, wire_value in option_pairs
                    if str(wire_value) == str(param.value)
                }
                visible_labels = {
                    str(value)
                    for item in scoped_evidence
                    for value in [
                        item.get("value"), item.get("selected"), item.get("selected_label"),
                        *(item.get("sample_values") or []),
                    ]
                    if value not in (None, "")
                }
                if matching_controls and mapped_labels.intersection(visible_labels):
                    scoped.setdefault(step.step_id, []).append(target)

        # An exact request observation owns its field directly. A route-scoped
        # control may reuse the same complete dictionary only when it identifies
        # exactly one matching request field on that step; ambiguity fails closed.
        targets = list(direct)
        targets.extend(items[0] for items in scoped.values() if len(items) == 1)
        return list({(item["step_id"], item["wire_path"]): item for item in targets}.values())

    expanded_page_options: dict[str, Any] = {}
    for raw_key, raw in page_options.items():
        targets = grounded_targets(raw) if isinstance(raw, dict) else []
        if not targets:
            expanded_page_options[str(raw_key)] = raw
            continue
        for index, target in enumerate(targets):
            expanded_page_options[f"{raw_key}@target:{index}"] = {
                **raw,
                "_grounded_target": target,
            }
    page_options = expanded_page_options

    seen: set[str] = set()
    for raw_key, raw in page_options.items():
        if isinstance(raw, dict):
            source_kind = str(raw.get("enum_source") or "dom").strip()
            mapping_complete = raw.get("mapping_complete") is True
            if (
                str(raw.get("control_kind") or "").lower() != "select"
                or source_kind not in {"dom", "script_static", "script_dictionary"}
                or (not mapping_complete and source_kind != "dom")
                or (source_kind == "script_static" and not raw.get("script_url"))
                or (
                    source_kind == "script_dictionary"
                    and (not raw.get("source_url") or not raw.get("dict_type"))
                )
            ):
                continue
            options = list(raw.get("options") or raw.get("values") or [])
            field_key = str(raw.get("field_key") or raw_key or "").strip()
            field_aliases = [
                str(value).strip() for value in (raw.get("field_aliases") or [])
                if str(value or "").strip()
            ]
            selected = str(raw.get("selected_label") or raw.get("selected") or "").strip()
            explicit_map = dict(raw.get("option_map") or raw.get("value_map") or {})
            grounded_target = raw.get("_grounded_target")
            strict_control_identity = True
        else:
            continue
        if not field_key or not options:
            continue
        option_pairs = [_enum_label_value(option) for option in options]
        if (
            any(pair is None for pair in option_pairs)
            or (mapping_complete and any(pair[1] is None for pair in option_pairs if pair))
            or len({str(pair[0]) for pair in option_pairs if pair}) != len(options)
        ):
            continue
        signature = json.dumps(
            {
                "field": field_key,
                "aliases": field_aliases,
                "selected": selected,
                "options": options,
                "grounded_target": grounded_target,
            },
            ensure_ascii=False, sort_keys=True, default=str,
        )
        if signature in seen:
            continue
        seen.add(signature)

        candidates: list[tuple[int, FlowStep, ParamField]] = []
        field_norm = norm(field_key)
        for step in spec.steps:
            for param in step.params or []:
                if isinstance(grounded_target, dict):
                    step_request_id = str((step.source_meta or {}).get("request_id") or "")
                    if not (
                        step.step_id == str(grounded_target.get("step_id") or "")
                        and step_request_id == str(grounded_target.get("request_id") or "")
                        and wire_identity(param.path) == wire_identity(grounded_target.get("wire_path"))
                    ):
                        continue
                names = [
                    param.key, param.label, param.path,
                    _strip_body_prefix(param.path or ""),
                    _strip_body_prefix(param.path or "").split(".")[-1],
                ]
                normalized_names = {norm(name) for name in names if str(name or "")}
                semantic_score = 0
                if isinstance(grounded_target, dict):
                    semantic_score = 12
                elif field_aliases:
                    if any(norm(alias) in normalized_names for alias in field_aliases if norm(alias)):
                        semantic_score = 10
                elif strict_control_identity:
                    semantic_score = 0
                elif field_norm and field_norm in normalized_names:
                    semantic_score = 8
                if not semantic_score:
                    continue
                if selected and param.value not in (None, "") and str(param.value) == selected:
                    semantic_score += 2
                candidates.append((semantic_score, step, param))
        if not candidates:
            continue
        best_score = max(score for score, _step, _param in candidates)
        best = [(step, param) for score, step, param in candidates if score == best_score]
        if len(best) != 1:
            continue
        step, param = best[0]
        if any(
            isinstance(item, dict)
            and item.get("source") == "manual_edit"
            and item.get("field") in {
                "type", "category", "source_kind", "source", "enum_options", "enum_value_map",
            }
            for item in (param.evidence or [])
        ):
            # This recovery pass does not rebuild ``step.selects``.  Preserve
            # the operator-owned field contract and leave any existing binding
            # untouched; do not manufacture a new inferred enum here.
            continue

        existing_binding = next((
            item for item in (step.selects or [])
            if _strip_body_prefix(item.path or item.id_path or "") == _strip_body_prefix(param.path)
        ), None)
        existing_param_source = dict(param.source or {})
        if (
            source_kind == "dom"
            and not mapping_complete
            and param.source_kind == "api_option"
            and existing_param_source.get("source_url")
            and existing_param_source.get("value_key")
            and existing_param_source.get("label_key")
            and param.enum_value_map
        ):
            # A label-only popup snapshot proves the visible control but not
            # its wire mapping. It cannot replace an already executable API
            # contract merely because the SelectBinding was absent or stale.
            continue
        if (
            existing_binding is not None
            and str(existing_binding.enum_source or "") == "api"
            and existing_binding.enum_confirmed is True
            and source_kind == "dom"
        ):
            # A recorded API label/value contract contains the actual wire
            # values. A later incomplete DOM snapshot is display evidence, not
            # authority to erase that stronger renewable source contract.
            continue
        before_state = (
            existing_binding.model_dump(mode="json") if existing_binding is not None else None,
            param.key,
            param.label,
            copy.deepcopy(step.sample_inputs),
        )
        option_map = dict(explicit_map)
        for option in options:
            # A bare string proves only a visible label, not that the backend
            # accepts the same string. Keep mappings only when the DOM exposed
            # an explicit value or when this recording proves selected→wire.
            if isinstance(option, dict) and "value" in option and option.get("value") is not None:
                label = option.get("label") if option.get("label") is not None else option.get("name")
                if label not in (None, ""):
                    option_map.setdefault(str(label), option.get("value"))
            elif isinstance(option, (list, tuple)) and len(option) >= 2 and option[1] is not None:
                option_map.setdefault(str(option[0]), option[1])
        if selected and param.value not in (None, ""):
            option_map.setdefault(selected, param.value)
        labels = [
            str(pair[0]) for option in options
            if (pair := _enum_label_value(option)) is not None
        ]
        confirmed = bool(
            mapping_complete
            and labels
            and all(label in option_map and option_map[label] is not None for label in labels)
        )

        binding = existing_binding
        if binding is None:
            binding = SelectBinding(path=param.path)
            step.selects.append(binding)
        compiled_call_key = any(
            isinstance(item, dict)
            and item.get("source") == "capability_compiler"
            and item.get("field") == "key"
            for item in (param.evidence or [])
        )
        binding.param = param.key if compiled_call_key else field_key
        binding.path = param.path
        binding.options = options
        binding.option_map = option_map or None
        if source_kind == "script_dictionary":
            source_changed = (
                _request_path({"url": str(binding.source_url or "")})
                != _request_path({"url": str(raw.get("source_url") or "")})
            )
            binding.source_url = str(raw.get("source_url") or "")
            binding.source_method = "GET"
            binding.value_key = "value"
            binding.label_key = "label"
            binding.category_key = "dictType"
            binding.category_value = str(raw.get("dict_type") or "")
            binding.enum_source = "api"
            if source_changed:
                # Transport identity belongs to the old endpoint. Keeping its
                # request id while replacing only the URL creates a hybrid
                # source that never existed in the recording.
                binding.source_request_id = ""
                binding.source_headers = {}
                binding.source_body = None
                binding.source_content_type = ""
                binding.source_role = ""
            _hydrate_select_source_contract(spec, binding)
        elif source_kind == "script_static":
            binding.source_url = ""
            binding.value_key = ""
            binding.label_key = ""
            binding.category_key = None
            binding.category_value = None
            binding.enum_source = "script_static"
        else:
            binding.source_url = ""
            binding.value_key = ""
            binding.label_key = ""
            binding.category_key = None
            binding.category_value = None
            binding.enum_source = "dom"
        binding.enum_confirmed = confirmed

        # DOM label is stronger public naming evidence than an internal wire
        # identifier, but never overwrite an explicit/manual business label.
        path_leaf = _strip_body_prefix(param.path or "").split(".")[-1]
        if (
            not compiled_call_key
            and (param.key in {"", param.path, path_leaf} or looks_internal_param_name(param.key))
        ):
            if not any(other is not param and other.key == field_key for other in step.params):
                old_key = param.key
                param.key = field_key
                param.label = field_key
                if old_key in step.sample_inputs and field_key not in step.sample_inputs:
                    step.sample_inputs[field_key] = step.sample_inputs.pop(old_key)
        after_state = (
            binding.model_dump(mode="json"),
            param.key,
            param.label,
            step.sample_inputs,
        )
        changed = changed or before_state != after_state

    return changed


def _page_enum_contract_for_param(
    spec: FlowSpec,
    step: FlowStep,
    param: ParamField,
    binding: SelectBinding,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]] | None:
    """Return a page enum only when ownership and the full wire map are proven."""
    page_options = _page_enum_options_from_request_facts(spec.request_facts)
    def normalized(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()
    param_names = {
        normalized(value) for value in (
            param.path, _strip_body_prefix(param.path or ""),
            _strip_body_prefix(param.path or "").split(".")[-1],
        ) if normalized(value)
    }
    keys = [
        binding.path, binding.id_path, param.path, param.key, param.label,
        _strip_body_prefix(binding.path or ""), _strip_body_prefix(param.path or ""),
    ]
    for key in [str(value or "") for value in keys if str(value or "")]:
        raw = page_options.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            # Legacy list/label-only snapshots are useful diagnostics but do
            # not prove control ownership or the backend wire values.
            continue
        if (
            str(raw.get("control_kind") or "").lower() != "select"
            or raw.get("mapping_complete") is not True
            or not _recording_evidence_matches_request(step.source_meta or {}, raw)
        ):
            continue
        aliases = [normalized(value) for value in (raw.get("field_aliases") or []) if normalized(value)]
        field_key = normalized(raw.get("field_key"))
        if aliases:
            if not any(alias in param_names for alias in aliases):
                continue
        elif not field_key or field_key not in param_names:
            continue
        source = str(raw.get("enum_source") or "dom").strip()
        if source == "script_dictionary":
            if not raw.get("source_url") or not raw.get("dict_type"):
                continue
        elif source == "script_static":
            if not raw.get("script_url"):
                continue
        elif source != "dom":
            continue
        options = list(raw.get("options") or raw.get("values") or [])
        if len(options) < 2:
            continue
        value_map: dict[str, Any] = {}
        valid = True
        for option in options:
            pair = _enum_label_value(option)
            if pair is None or pair[0] in value_map or pair[1] is None:
                valid = False
                break
            value_map[str(pair[0])] = pair[1]
        explicit_map = dict(raw.get("option_map") or raw.get("value_map") or {})
        if not valid or len(value_map) != len(options):
            continue
        if any(
            label in explicit_map and str(explicit_map[label]) != str(value)
            for label, value in value_map.items()
        ):
            continue
        selected_label = str(raw.get("selected_label") or raw.get("selected") or "").strip()
        selected_value = raw.get("selected_value")
        current = param.value
        wire_value = selected_value if selected_value not in (None, "") else current
        if (
            selected_value not in (None, "")
            and current not in (None, "")
            and str(selected_value) != str(current)
        ):
            continue
        if selected_label:
            if selected_label not in value_map or wire_value in (None, "") or str(value_map[selected_label]) != str(wire_value):
                continue
        elif current not in (None, ""):
            if sum(str(value) == str(current) for value in value_map.values()) != 1:
                continue
        return options, value_map, {
            "enum_source": source,
            "source_url": str(raw.get("source_url") or ""),
            "dict_type": str(raw.get("dict_type") or ""),
            "script_url": str(raw.get("script_url") or ""),
        }
    return None


def _sync_step_option_contracts(spec: FlowSpec, step: FlowStep) -> None:
    """Project executable select bindings back onto their request parameters.

    SelectBinding is the grounded evidence for page/API choices.  Keeping only
    the ParamField as ``user_input`` loses label-to-value mapping and the source
    request when capabilities are rebuilt.
    """
    step.selects = [
        binding for binding in (step.selects or [])
        if not _looks_pagination_field(
            str(binding.param or ""), str(binding.path or binding.id_path or ""),
        )
    ]
    for param in step.params or []:
        if param.type in _ENUM_PARAM_TYPES or param.source_kind in _ENUM_SOURCE_KINDS or param.source_kind == "api_option":
            continue
        if not _param_axis_manually_edited(param, "enum_options", "enum_value_map"):
            param.enum_options = None
            param.enum_value_map = None
        if not _param_field_manually_edited(param, "description"):
            param.description = _strip_option_descriptions(param.description) or None
        if not _param_field_manually_edited(param, "reason"):
            param.reason = _strip_option_descriptions(param.reason)
    grounded_bindings: list[SelectBinding] = []
    for binding in step.selects or []:
        _hydrate_select_source_contract(spec, binding)
        # Paired controls commonly have both ``name`` and ``id`` leaves.  The
        # caller-facing option contract belongs to the display/name path; the ID
        # remains a runtime-derived request field.  Only use id_path when there is
        # no separate display path in the request.
        param = next((
            item for item in (step.params or [])
            if binding.path and item.path == binding.path
        ), None)
        if param is None:
            param = next((
                item for item in (step.params or [])
                if binding.id_path and item.path == binding.id_path
            ), None)
        if param is None:
            continue
        if param.source_kind in {"selected_option_field", "computed"}:
            continue
        if param.locked:
            grounded_bindings.append(binding)
            continue
        type_owned = _param_field_manually_edited(param, "type")
        category_owned = _param_axis_manually_edited(
            param, "category", "exposed_to_user", "editable",
        )
        source_owned = _param_axis_manually_edited(param, "source_kind", "source")
        options_owned = _param_axis_manually_edited(param, "enum_options", "enum_value_map")
        page_contract = _page_enum_contract_for_param(spec, step, param, binding)
        if page_contract:
            page_options, page_value_map, page_meta = page_contract
            page_source = str(page_meta.get("enum_source") or "dom")
            binding.options = copy.deepcopy(page_options)
            binding.option_map = dict(page_value_map)
            binding.count = len(page_options)
            binding.enum_confirmed = True
            if page_source == "script_dictionary":
                source_changed = str(binding.source_url or "") != str(page_meta.get("source_url") or "")
                binding.source_url = str(page_meta.get("source_url") or "")
                binding.source_method = "GET"
                binding.value_key = "value"
                binding.label_key = "label"
                binding.category_key = "dictType"
                binding.category_value = str(page_meta.get("dict_type") or "")
                binding.enum_source = "api"
                if source_changed:
                    binding.source_headers = {}
                    binding.source_body = None
                    binding.source_content_type = ""
                    binding.source_role = ""
                    binding.source_request_id = ""
                _hydrate_select_source_contract(spec, binding)
            else:
                # A complete DOM/static map is self-contained.  Keeping an old
                # guessed endpoint here lets unknown labels fall through to a
                # foreign API at runtime, so clear every stale dynamic source.
                binding.source_url = ""
                binding.source_method = "GET"
                binding.source_headers = {}
                binding.source_body = None
                binding.source_content_type = ""
                binding.source_role = ""
                binding.source_request_id = ""
                binding.value_key = ""
                binding.label_key = ""
                binding.category_key = None
                binding.category_value = None
                binding.enum_source = "script_static" if page_source == "script_static" else "dom"
        source_path = _request_path({"url": binding.source_url}) if binding.source_url else ""
        captured_source = any(
            fact.response_json is not None
            and (fact.method or "GET").upper() in {"GET", "HEAD"}
            and _request_path({"url": fact.path or fact.url}) == source_path
            for fact in (spec.request_facts.requests or [])
        ) if source_path else False
        api_contract = bool(
            binding.source_url
            and binding.value_key
            and binding.label_key
            and (captured_source or binding.option_map or binding.options)
            and str(binding.enum_source or "api") == "api"
        )
        static_contract = bool(
            str(binding.enum_source or "") == "script_static"
            and (binding.option_map or binding.options)
        )
        dom_contract = bool(
            page_contract
            or (
                str(binding.enum_source or "") == "dom"
                and (binding.option_map or binding.options)
            )
        )
        manual_contract = bool(
            str(binding.enum_source or "") == "manual"
            and (binding.option_map or binding.options)
        )
        # A repeating object container is the caller-owned transport shape,
        # while this binding only resolves a selector inside each row.  Keep
        # the binding executable without projecting its enum contract onto the
        # array container itself; row leaf contracts carry that option source.
        if (
            str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
            and str((param.source or {}).get("structure_kind") or "") == "array_object"
        ):
            grounded_bindings.append(binding)
            continue
        if not (api_contract or static_contract or dom_contract or manual_contract):
            # A field name, a numeric sample, or a URL without a captured
            # label/value contract is not enum evidence. Preserve the binding
            # itself so a user can finish/edit the configuration without the
            # next sync silently deleting it, but keep it unconfirmed and do
            # not project it as an executable enum contract.
            binding.enum_confirmed = False
            grounded_bindings.append(binding)
            if not type_owned and not source_owned:
                param.type = param.wire_type or _infer_type_from_value(param.value)
            if not options_owned and not source_owned:
                param.enum_options = None
                param.enum_value_map = None
            if param.category == "user_param" and not type_owned:
                if not source_owned:
                    param.source_kind = "user_input"
                    param.source = {"kind": "sample", "path": param.path}
                if not category_owned:
                    param.exposed_to_user = True
                    param.editable = True
            if not _param_field_manually_edited(param, "need_human_confirm"):
                param.need_human_confirm = False
            if not _param_field_manually_edited(param, "description"):
                param.description = _strip_option_descriptions(param.description) or None
            if not _param_field_manually_edited(param, "reason"):
                param.reason = _strip_option_descriptions(param.reason)
            continue
        source_kind = (
            # A captured option endpoint is the stronger and renewable source.
            # Its DOM snapshot remains evidence/default material, but must not
            # hide the live source from the exported contract.
            "api_option" if api_contract
            else "page_enum" if dom_contract
            else "manual_enum" if manual_contract
            else "static_enum"
        )
        grounded_bindings.append(binding)
        options = list(page_contract[0]) if page_contract else _enum_options_for_param(binding)
        option_map = dict(page_contract[1]) if page_contract else (_enum_value_map_for_param(binding) or {})
        if page_contract:
            page_labels = {
                str(pair[0]) for item in page_contract[0]
                if (pair := _enum_label_value(item)) is not None
            }
            option_map.update({
                str(label): value for label, value in (_enum_value_map_for_param(binding) or {}).items()
                if str(label) in page_labels and value is not None
            })
        # Every grounded choice is an enum in the caller-facing business
        # contract. The recorded scalar/array transport remains independently
        # available in wire_type for request serialization.
        if not param.wire_type:
            param.wire_type = param.type
        if not type_owned:
            param.type = "list-enum" if binding.multi else "enum"
        if param.source_kind == "selected_option_field" and not category_owned:
            _apply_selected_option_field_caller_ownership(param)
        elif (
            not category_owned
            and param.category not in {"runtime_var", "system_const"}
            and param.source_kind != "selected_option_field"
        ):
            param.category = "user_param"
            param.exposed_to_user = True
            param.editable = True
        keep_hydration = (
            param.source_kind == "previous_response"
            and bool(
                (param.source or {}).get("link_id")
                or (param.source or {}).get("allow_caller_override")
                or (param.source or {}).get("option_source")
            )
        )
        if not source_owned and not keep_hydration:
            param.source_kind = source_kind
        if not options_owned and source_kind == "api_option":
            # The selected API is authoritative, including an empty result.
            # Never resurrect candidates captured from the previously selected
            # endpoint after a source change.
            param.enum_options = list(options or []) or None
            param.enum_value_map = dict(option_map or {}) or None
        elif not options_owned:
            param.enum_options = list(options or param.enum_options or []) or None
            param.enum_value_map = dict(option_map or param.enum_value_map or {}) or None
        if not source_owned:
            option_contract = {
                "kind": source_kind,
                "source_url": binding.source_url if source_kind == "api_option" else None,
                "source_method": binding.source_method,
                "source_request_id": binding.source_request_id,
                "value_key": binding.value_key,
                "label_key": binding.label_key,
                "category_key": binding.category_key,
                "category_value": binding.category_value,
                "id_path": binding.id_path or binding.path or param.path,
                "enum_source": (
                    "dom" if source_kind == "page_enum"
                    else "script_static" if source_kind == "static_enum"
                    else "manual" if source_kind == "manual_enum"
                    else "api"
                ),
                "enum_confirmed": (
                    len(option_map) == len(options or [])
                    if page_contract
                    else (binding.enum_confirmed if binding.enum_confirmed is not None else True)
                ),
            }
            if keep_hydration:
                allow_caller_override = bool(
                    (param.source or {}).get("allow_caller_override")
                )
                param.source_kind = "previous_response"
                param.source = {
                    **dict(param.source or {}),
                    "kind": "previous_response",
                    "allow_caller_override": allow_caller_override,
                    "option_source": option_contract,
                }
            else:
                param.source = {**dict(param.source or {}), **option_contract}
        if not _param_field_manually_edited(param, "need_human_confirm"):
            param.need_human_confirm = bool(
                source_kind == "unknown"
                or (
                    source_kind == "page_enum"
                    and (param.source or {}).get("enum_confirmed") is False
                )
            )
        source_reason = (
            "候选来自录制捕获的只读接口；调用方传显示值，运行期按当前接口结果映射真实值"
            if source_kind == "api_option"
            else (
                "候选来自页面真实下拉；调用方传显示值，运行期按录制的 label/value 映射真实值"
                if source_kind == "page_enum"
                else "候选接口缺少可信的 label/value 证据，不能作为已确认枚举来源"
            )
        )
        option_description = _enum_options_description(source_kind, param.enum_options, param.enum_value_map)
        param.description = _upsert_option_description(param.description, option_description)
        param.reason = _upsert_option_description(param.reason or source_reason, option_description)
    step.selects = grounded_bindings
    if int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2:
        _sync_selected_option_field_projections(step)


def _sync_selected_option_field_projections(step: FlowStep) -> None:
    """Keep field origin and executable option projection as one contract."""
    bindings = list(step.selects or [])
    for param in step.params or []:
        if param.source_kind != "selected_option_field":
            continue
        _apply_selected_option_field_caller_ownership(param)
        source = dict(param.source or {})
        target_path = str(source.get("target_path") or param.path or "")
        response_path = str(source.get("response_path") or "")
        selector_path = str(source.get("selector_path") or "")
        selector_param = str(source.get("selector_param") or "")
        source_request_id = str(source.get("source_request_id") or "")
        source_url = str(source.get("source_url") or "")
        if not target_path or not response_path:
            continue
        matches = [
            binding for binding in bindings
            if (
                selector_path
                and selector_path in {str(binding.path or ""), str(binding.id_path or "")}
            ) or (
                selector_param
                and selector_param == str(binding.param or "")
            )
        ]
        if not matches and source_request_id:
            matches = [
                binding for binding in bindings
                if source_request_id == str(binding.source_request_id or "")
            ]
        if not matches and source_url:
            source_path = _request_path({"url": source_url})
            matches = [
                binding for binding in bindings
                if source_path == _request_path({"url": str(binding.source_url or "")})
            ]
        if len(matches) != 1:
            continue
        matches[0].field_projections[target_path] = response_path


def _refresh_api_option_display_labels(spec: FlowSpec) -> int:
    """Repair persisted live-option labels from their captured response rows."""
    snapshots: list[tuple[float, str, str, list[dict[str, Any]]]] = []
    for fact in (spec.request_facts or RequestFacts()).requests or []:
        rows = [
            dict(item) for item in (as_list_payload(fact.response_json) or [])
            if isinstance(item, dict)
        ]
        if rows:
            snapshots.append((
                _request_sequence_value(fact.sequence) or -1.0,
                str(fact.request_id or ""),
                _option_source_contract_endpoint(fact.url or fact.path),
                rows,
            ))
    for step in spec.steps or []:
        rows = [
            dict(item) for item in (as_list_payload(step.response_json) or [])
            if isinstance(item, dict)
        ]
        if rows:
            snapshots.append((
                _step_sequence(step) or -1.0,
                str((step.source_meta or {}).get("request_id") or ""),
                _option_source_contract_endpoint(step.url or step.path),
                rows,
            ))

    repaired = 0
    for step in spec.steps or []:
        for param in step.params or []:
            if (
                param.source_kind != "api_option"
                or param.locked
                or _param_has_manual_contract(param)
            ):
                continue
            source = dict(param.source or {})
            value_key = str(source.get("value_key") or "")
            current_label = str(source.get("label_key") or "")
            if not value_key or (
                current_label
                and current_label != value_key
                and not _is_idlike(current_label)
            ):
                continue
            source_request_id = str(source.get("source_request_id") or "")
            source_endpoint = _option_source_contract_endpoint(
                str(source.get("source_url") or "")
            )
            matches = [
                item for item in snapshots
                if (
                    source_request_id
                    and item[1] == source_request_id
                ) or (
                    source_endpoint
                    and item[2] == source_endpoint
                )
            ]
            if not matches:
                continue
            _sequence, _request_id, _endpoint, rows = max(matches, key=lambda item: item[0])
            selected_rows = [
                row for row in rows
                if _recorded_scalar_values_match(
                    _flow_path_lookup(row, value_key), param.value,
                )
            ]
            sample = selected_rows[0] if len(selected_rows) == 1 else rows[0]
            label_key = _pick_label_key(sample, value_key)
            if label_key == value_key or (
                _is_idlike(label_key)
                and not re.search(r"(?:code|no|number|serial)$", label_key, re.I)
            ):
                continue
            records: list[dict[str, Any]] = []
            label_values: dict[str, Any] = {}
            ambiguous = False
            for row in rows:
                value = _flow_path_lookup(row, value_key)
                label = _flow_path_lookup(row, label_key)
                if value is _FLOW_PATH_MISSING or label is _FLOW_PATH_MISSING:
                    continue
                label_text = str(label or "").strip()
                if not label_text:
                    continue
                if label_text in label_values and label_values[label_text] != value:
                    ambiguous = True
                    break
                label_values[label_text] = value
            if ambiguous or not label_values:
                continue
            records = [
                {"label": label, "value": value}
                for label, value in label_values.items()
            ]
            param.source = {**source, "label_key": label_key}
            param.enum_options = records
            param.enum_value_map = dict(label_values)
            binding = _find_select_binding(step, param)
            if binding is not None:
                binding.label_key = label_key
                binding.options = records
                binding.option_map = dict(label_values)
                binding.count = len(records)
            repaired += 1
    return repaired


def _find_select_binding(step: FlowStep, param: ParamField) -> SelectBinding | None:
    for sel in step.selects:
        if sel.path == param.path or (sel.id_path and sel.id_path == param.path):
            return sel
    return None


def _source_url_matches_request(source_url: str, request_url: str, request_path: str) -> bool:
    """Match an option endpoint contract without pretending it is an occurrence ID."""
    candidate_url = str(request_url or request_path or "")
    if normalized_request_path(source_url) != normalized_request_path(candidate_url):
        return False
    source_host = urlparse(str(source_url or "")).netloc.casefold()
    candidate_host = urlparse(candidate_url).netloc.casefold()
    return not (source_host and candidate_host and source_host != candidate_host)


def _page_control_source_request_ids(param: ParamField) -> set[str]:
    owned: set[str] = set()
    for item in param.evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "page_control":
            continue
        if str(item.get("binding_status") or "bound") != "bound":
            continue
        values = item.get("source_request_ids") or []
        if not isinstance(values, list):
            values = [values]
        values = [*values, item.get("source_request_id")]
        owned.update(str(value) for value in values if value)
    return owned


def _bind_option_source(
    spec: FlowSpec,
    *,
    target_step_id: str,
    target_path: str,
    source_step_id: str = "",
    source_url: str = "",
    source_request_id: str = "",
    value_key: str = "",
    label_key: str = "",
    category_key: str | None = None,
    category_value: Any = None,
    id_path: str = "",
    options: list[Any] | None = None,
    option_map: dict[str, Any] | None = None,
    multi: bool = False,
    actor: str = "system",
) -> None:
    step = _find_step(spec, target_step_id)
    param = _find_param(step, target_path)
    if param.source_kind in {"selected_option_field", "computed"}:
        return
    normalized_actor = str(actor or "system").strip().lower()
    automated = normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS
    if automated and (
        param.locked or _param_axis_manually_edited(param, "source_kind", "source")
    ):
        return
    source_step = _find_step(spec, source_step_id) if source_step_id else None
    step_request_id = str((source_step.source_meta or {}).get("request_id") or "") if source_step else ""
    if source_request_id and step_request_id and source_request_id != step_request_id:
        raise ValueError("bind_option_source source step and request identities conflict")
    resolved_request_id = source_request_id or step_request_id
    source_fact = next(
        (fact for fact in spec.request_facts.requests or [] if fact.request_id == resolved_request_id),
        None,
    ) if resolved_request_id else None
    if resolved_request_id and source_fact is None and not source_step:
        raise ValueError("bind_option_source source request does not belong to FlowSpec")
    if source_fact is not None and source_url and not _source_url_matches_request(
        source_url, source_fact.url, source_fact.path,
    ):
        raise ValueError("bind_option_source source request conflicts with source_url")
    owned_request_ids = _page_control_source_request_ids(param)
    if resolved_request_id and owned_request_ids and resolved_request_id not in owned_request_ids:
        raise ValueError("bind_option_source source request does not own target field")
    src_url = source_url or (source_step.path or source_step.url if source_step else "")
    if not src_url and source_fact is not None:
        src_url = source_fact.path or source_fact.url
    if not src_url:
        raise ValueError("bind_option_source missing source_url/source_step")

    category_owned = automated and _param_axis_manually_edited(
        param, "category", "exposed_to_user", "editable",
    )
    type_owned = automated and _param_field_manually_edited(param, "type")
    options_owned = automated and _param_axis_manually_edited(
        param, "enum_options", "enum_value_map",
    )
    keep_hydration = (
        param.source_kind == "previous_response"
        and bool((param.source or {}).get("allow_caller_override") or (param.source or {}).get("link_id"))
    )
    option_contract = {
        "kind": "api_option",
        "source_step_id": source_step_id,
        "source_request_id": resolved_request_id,
        "source_url": src_url,
        "value_key": value_key,
        "label_key": label_key,
        "category_key": category_key,
        "category_value": category_value,
        "id_path": id_path or param.path,
    }
    if not category_owned:
        param.category = "user_param"
    # ``type`` is the caller-facing business contract; ``wire_type`` retains
    # the recorded JSON scalar transported to the backend.
    if not param.wire_type:
        param.wire_type = param.type
    if not type_owned:
        param.type = "list-enum" if multi else "enum"
    if not category_owned:
        param.exposed_to_user = True
        param.editable = True
    param.need_human_confirm = False
    if keep_hydration:
        param.source_kind = "previous_response"
        param.source = {
            **dict(param.source or {}),
            "kind": "previous_response",
            "allow_caller_override": True,
            "option_source": option_contract,
        }
        param.reason = (
            "编辑场景默认来自上游详情，候选来自接口选项源；"
            "调用方可改，显式输入优先于上游默认值"
        )
    else:
        param.source_kind = "api_option"
        param.source = option_contract
        param.reason = "字段候选来自接口选项源，调用方传显示值，运行期按 label/value 映射提交真实值"
    if options and not options_owned:
        param.enum_options = list(options)
    if option_map and not options_owned:
        param.enum_value_map = dict(option_map)
    option_evidence = {
        "source": "option_source",
        "source_step_id": source_step_id,
        "source_request_id": resolved_request_id,
        "source_url": src_url,
        "value_key": value_key,
        "label_key": label_key,
        "category_key": category_key,
        "category_value": category_value,
    }
    if option_evidence not in param.evidence:
        param.evidence.append(option_evidence)

    sel = _find_select_binding(step, param)
    if sel is None:
        sel = SelectBinding(param=param.key, path=param.path)
        step.selects.append(sel)
    sel.param = param.key
    sel.path = param.path
    sel.source_url = src_url
    sel.source_request_id = resolved_request_id or sel.source_request_id
    sel.value_key = value_key or sel.value_key
    sel.label_key = label_key or sel.label_key
    sel.category_key = category_key
    sel.category_value = None if category_value is None else str(category_value)
    sel.id_path = id_path or sel.id_path or param.path
    sel.multi = bool(multi)
    if options:
        sel.options = list(options)
        sel.count = len(options)
    if option_map:
        sel.option_map = dict(option_map)
    sel.enum_source = "api"
    sel.enum_confirmed = True
    sel.actor = normalized_actor if normalized_actor in {"user", "agent", "planner", "repair"} else "heuristic"
    sel.confidence = max(float(sel.confidence or 0), 1.0 if normalized_actor == "user" else 0.8)
    _hydrate_select_source_contract(spec, sel)
    if normalized_actor == "user":
        manual_fields = [
            "type", "category", "source_kind", "source", "exposed_to_user",
            "editable", "need_human_confirm",
        ]
        if options:
            manual_fields.append("enum_options")
        if option_map:
            manual_fields.append("enum_value_map")
        _record_param_manual_contract(param, manual_fields)


def _hydrate_select_source_contract(spec: FlowSpec, binding: SelectBinding) -> None:
    """把界面选择的捕获接口补成可执行选项源，而不是只保存一个 URL。"""
    if not binding.source_url:
        return
    source_identity: dict[str, Any] = {
        "source_url": binding.source_url,
        "source_request_id": binding.source_request_id,
        "source_method": binding.source_method,
    }
    if binding.source_body is not None:
        source_identity["source_body"] = binding.source_body
    if binding.source_content_type:
        source_identity["source_content_type"] = binding.source_content_type
    fact = unique_request_identity_match(source_identity, (
        (candidate, {
            **candidate.model_dump(exclude_none=True),
            "request_id": candidate.request_id,
            "method": candidate.method,
            "url": candidate.url or candidate.path,
            "path": candidate.path,
            "post_data": candidate.post_data,
            "content_type": candidate.content_type,
        })
        for candidate in (spec.request_facts.requests or [])
    ))
    if fact is None:
        return
    source_changed = bool(binding.source_request_id and binding.source_request_id != (fact.request_id or ""))
    analysis = spec.request_facts.analysis.get(fact.request_id) if fact.request_id else None
    role = analysis.role if analysis is not None else ""
    safe_headers = {
        str(key): value for key, value in (fact.headers or {}).items()
        if str(key).lower() not in {
            "authorization", "cookie", "set-cookie", "x-auth-token", "x-access-token",
            "content-length", "host", "origin", "referer",
        }
    }
    binding.source_method = (fact.method or "GET").upper()
    binding.source_headers = safe_headers
    binding.source_body = fact.post_data
    binding.source_content_type = fact.content_type or ""
    binding.source_role = role
    binding.source_request_id = fact.request_id or ""
    binding.enum_source = "api"

    # Initial capture already applies field-specific filtering (for example a
    # shared dictionary endpoint narrowed to one dictType). Preserve that
    # grounded subset. A changed source or an empty snapshot is rehydrated.
    if not source_changed and (binding.options or binding.option_map):
        return

    # Refresh the captured candidate snapshot whenever the interface is
    # selected/reselected. Runtime execution may legitimately return no rows;
    # in that case an empty snapshot is authoritative rather than an error.
    items = as_list_payload(fact.response_json) or []
    if binding.category_key and binding.category_value is not None:
        items = [
            item for item in items
            if isinstance(item, dict)
            and str(item.get(binding.category_key)) == str(binding.category_value)
        ]
    if not items:
        binding.options = []
        binding.option_map = None
        binding.count = 0
        binding.enum_confirmed = True
        return

    first = items[0]
    if not isinstance(first, dict):
        records = [{"label": str(item), "value": item} for item in items[:200]]
        binding.options = records
        binding.option_map = {record["label"]: record["value"] for record in records}
        binding.count = len(items)
        binding.enum_confirmed = True
        return

    keys = list(first.keys())
    value_candidates = [binding.value_key, "value", "id", "code", "dictValue", "key"]
    label_candidates = [binding.label_key, "label", "name", "text", "title", "dictLabel"]
    value_key = next((key for key in value_candidates if key and key in first), "")
    label_key = next((key for key in label_candidates if key and key in first and key != value_key), "")
    if not value_key:
        value_key = next((key for key in keys if not isinstance(first.get(key), (dict, list))), "")
    if not label_key:
        label_key = next((
            key for key in keys
            if key != value_key and isinstance(first.get(key), str) and str(first.get(key) or "").strip()
        ), value_key)
    if not value_key:
        if source_changed:
            binding.options = []
            binding.option_map = None
            binding.count = 0
        return

    binding.value_key = value_key
    binding.label_key = label_key or value_key
    records: list[dict[str, Any]] = []
    option_map: dict[str, Any] = {}
    seen: set[tuple[str, str]] = set()
    for item in items[:200]:
        if not isinstance(item, dict) or value_key not in item:
            continue
        raw_value = item.get(value_key)
        raw_label = item.get(binding.label_key, raw_value)
        label = str(raw_label if raw_label not in (None, "") else raw_value)
        signature = (label, repr(raw_value))
        if not label or signature in seen:
            continue
        seen.add(signature)
        records.append({"label": label, "value": raw_value})
        option_map[label] = raw_value
    binding.options = records
    binding.option_map = option_map or None
    binding.count = len(items)
    binding.enum_confirmed = True

_PENDING_FLOW_SPEC_HELPERS = {'_AUTOMATED_FIELD_EDIT_ACTORS': 'dano.execution.page.flow_spec_core.controlled_edits', '_ENUM_PARAM_TYPES': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_ENUM_SOURCE_KINDS': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_FLOW_PATH_MISSING': 'dano.execution.page.flow_spec_core.normalization', '_enum_label_value': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_enum_options_description': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_enum_options_for_param': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_enum_value_map_for_param': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_find_param': 'dano.execution.page.flow_spec_core.controlled_edits', '_find_step': 'dano.execution.page.flow_spec_core.controlled_edits', '_flow_path_lookup': 'dano.execution.page.flow_spec_core.normalization', '_infer_type_from_value': 'dano.execution.page.flow_spec_core.normalization', '_option_source_contract_endpoint': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_record_param_manual_contract': 'dano.execution.page.flow_spec_core.controlled_edits', '_recorded_scalar_values_match': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization', '_strip_option_descriptions': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_upsert_option_description': 'dano.execution.page.flow_materialization.field_contracts.option_projection'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
