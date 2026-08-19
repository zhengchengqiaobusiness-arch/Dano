"""Stage 6: capability request refs, membership, and step indexes."""
from __future__ import annotations

from typing import Any
import copy
import hashlib
import re
from urllib.parse import urlparse
from dano.execution.page.flow_spec_core.models import (
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
    RequestFact,
    RequestFacts,
)
from dano.execution.page.recording_facts import (
    _REQUEST_OBSERVER_KEYS,
    _WRITE_METHODS,
    _has_query_action_evidence,
    _read_is_entity_enrichment_lookup,
    _request_fact_items,
    _request_path,
    _schema_from_response_value,
)
from dano.execution.page.flow_materialization.links import (
    _dependency_closure_step_ids,
    _flow_link_kind,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _is_option_source_url,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _stable_json_hash,
)
from dano.execution.page.flow_materialization.request_steps import (
    _step_sequence,
)


def _capability_scoped_node_step_ids(nodes: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        sid = str(node.get("step_id") or "").strip()
        if sid and sid not in ids:
            ids.append(sid)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            child = node.get(child_key)
            if isinstance(child, list):
                for child_sid in _capability_scoped_node_step_ids([n for n in child if isinstance(n, dict)]):
                    if child_sid not in ids:
                        ids.append(child_sid)
    return ids


def _capability_scoped_step_ids(cap: FlowCapability) -> list[str]:
    ids: list[str] = []
    for sid in _capability_scoped_node_step_ids(cap.nodes or []):
        sid = str(sid or "").strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _step_request_fact_for_capability(spec: FlowSpec, step: FlowStep) -> RequestFact | None:
    rid = str((step.source_meta or {}).get("request_id") or "").strip()
    if rid:
        found = next((f for f in spec.request_facts.requests if f.request_id == rid), None)
        if found is not None:
            return found
    request_index = (step.source_meta or {}).get("request_index")
    if request_index is not None:
        found = next((f for f in spec.request_facts.requests if f.request_index == request_index), None)
        if found is not None:
            return found
    method = (step.method or "").upper()
    path = _request_path({"url": step.path or step.url})
    return next(
        (
            f for f in spec.request_facts.requests
            if (f.method or "").upper() == method and _request_path({"url": f.path or f.url}) == path
        ),
        None,
    )


def _capability_request_ref_from_step(
    spec: FlowSpec,
    step: FlowStep,
    existing: CapabilityRequestRef | None = None,
    *,
    extra: dict[str, Any] | None = None,
) -> CapabilityRequestRef:
    fact = _step_request_fact_for_capability(spec, step)
    rid = fact.request_id if fact else str((step.source_meta or {}).get("request_id") or "")
    analysis = spec.request_facts.analysis.get(rid) if rid else None
    derived_usage = (
        "preflight"
        if bool((step.source_meta or {}).get("control_preflight_for_write"))
        else "execute"
    )
    usage = (
        existing.usage
        if existing and existing.origin in {"manual", "user", "compiler"}
        else derived_usage
    )
    role = (analysis.role if analysis else "") or (step.source_meta or {}).get("role") or step.semantic_role or ""
    if role == "submit_anchor":
        role = "business_write"
    elif derived_usage == "preflight" and role in {"", "business_get"}:
        role = "read_context"
    ref = CapabilityRequestRef(
        request_id=rid,
        request_index=fact.request_index if fact else (step.source_meta or {}).get("request_index"),
        step_id=step.step_id,
        role=role,
        method=(step.method or "").upper(),
        path=step.path or step.url,
        sequence=fact.sequence if fact else (step.source_meta or {}).get("sequence", (step.source_meta or {}).get("request_index")),
        confidence=float((analysis.confidence if analysis else None) or (step.source_meta or {}).get("confidence") or 0.0),
        reason=(analysis.reason if analysis else "") or (step.source_meta or {}).get("keep_reason") or "",
        usage=usage,
        origin=existing.origin if existing else str((step.source_meta or {}).get("membership_origin") or "planner"),
        confirmed=bool(existing.confirmed) if existing else False,
    )
    merged_extra: dict[str, Any] = {}
    if existing and existing.__pydantic_extra__:
        merged_extra.update(existing.__pydantic_extra__ or {})
    if extra:
        merged_extra.update(extra)
    if merged_extra:
        for key, value in merged_extra.items():
            if key in {
                "request_id",
                "request_index",
                "step_id",
                "role",
                "method",
                "path",
                "sequence",
                "confidence",
                "reason",
                "usage",
                "origin",
                "confirmed",
            }:
                continue
            ref.__pydantic_extra__[key] = value
    return ref


def _expand_response_key_map_inputs(
    spec: FlowSpec,
    capability: FlowCapability,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Expose one caller field per stable response label, never the wire map."""
    expanded = copy.deepcopy(schema or {"type": "object", "properties": {}, "required": []})
    properties = expanded.setdefault("properties", {})
    required = [str(name) for name in expanded.get("required") or []]
    member_ids = set(capability.step_ids or [])
    by_id = {step.step_id: step for step in spec.steps}
    reserved = set(properties)
    for link in spec.links or []:
        if (
            _flow_link_kind(link) != "response_key_map"
            or link.source_step_id not in member_ids
            or link.target_step_id not in member_ids
        ):
            continue
        binding = dict(link.value_binding or {})
        labels = [str(label) for label in binding.get("required_labels") or [] if str(label)]
        input_field = str(binding.get("input_field") or "")
        if not labels or not input_field:
            continue
        target = by_id.get(link.target_step_id)
        public = next((
            param for param in (target.params if target is not None else [])
            if _strip_body_prefix(str(param.path or ""))
            == _strip_body_prefix(str(link.target_container_path or link.target_path or ""))
        ), None)
        samples = public.value if public is not None and isinstance(public.value, dict) else {}
        properties.pop(input_field, None)
        reserved.discard(input_field)
        required = [name for name in required if name != input_field]
        field_map: dict[str, str] = {}
        existing_map = dict(binding.get("input_fields_by_label") or {})
        for label in labels:
            preferred = str(existing_map.get(label) or label)
            field_name = preferred
            if field_name in reserved:
                field_name = f"{input_field}.{label}"
            suffix = 2
            base_name = field_name
            while field_name in reserved:
                field_name = f"{base_name}_{suffix}"
                suffix += 1
            reserved.add(field_name)
            field_map[label] = field_name
            sample = samples.get(label)
            field_schema = _schema_from_response_value(sample)
            field_schema.update({
                "label": label,
                "description": f"为上游返回的“{label}”节点选择调用值",
                "x-dano-capability-owned": True,
                "x-dano-dynamic-key-map": {
                    "link_id": link.link_id,
                    "label": label,
                    "target_path": link.target_container_path or link.target_path,
                },
            })
            option_source = binding.get("option_source")
            if isinstance(option_source, dict) and option_source:
                field_schema["x-dano-option-source"] = copy.deepcopy(option_source)
                field_schema["x-options-source"] = True
            properties[field_name] = field_schema
            required.append(field_name)
        link.value_binding = {
            **binding,
            "input_fields_by_label": field_map,
        }
        if target is not None:
            target.sample_inputs.pop(input_field, None)
            for label, field_name in field_map.items():
                if label in samples:
                    target.sample_inputs[field_name] = copy.deepcopy(samples[label])
    expanded["required"] = list(dict.fromkeys(required))
    return expanded


def _step_evidence(step: FlowStep) -> dict[str, Any]:
    evidence = {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or step.url,
        "role": (step.source_meta or {}).get("role") or step.semantic_role,
    }
    for key in _REQUEST_OBSERVER_KEYS:
        value = (step.source_meta or {}).get(key)
        if value not in (None, ""):
            evidence[key] = value
    return evidence


def _capability_call_nodes(steps: list[FlowStep]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, 1):
        nodes.append({
            "id": f"call_{idx}",
            "type": "call",
            "step_id": step.step_id,
            "method": step.method,
            "path": step.path or step.url,
        })
    if steps:
        nodes.append({
            "id": "return_final",
            "type": "return",
            "from": steps[-1].step_id,
            "path": "response",
        })
    return nodes


def _option_source_step_ids(spec: FlowSpec) -> set[str]:
    ids: set[str] = set()
    urls: set[str] = set()
    request_ids: set[str] = set()
    for step in spec.steps:
        role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
        read = {
            "url": step.url or step.path,
            "path": step.path,
            "method": step.method,
            "role": role,
            "response_json": step.response_json,
            **dict(step.source_meta or {}),
        }
        if (
            not _read_is_entity_enrichment_lookup(read)
            and (
                role == "read_option"
                or _is_option_source_url(step.path or step.url)
            )
        ):
            ids.add(step.step_id)
        for param in step.params:
            if param.source_kind != "api_option":
                continue
            source = param.source or {}
            if source.get("source_step_id"):
                ids.add(str(source["source_step_id"]))
            if source.get("source_url"):
                urls.add(_request_path({"url": str(source["source_url"])}))
            if source.get("source_request_id"):
                request_ids.add(str(source["source_request_id"]))
        for select in step.selects:
            if select.source_url:
                urls.add(_request_path({"url": select.source_url}))
            if select.source_request_id:
                request_ids.add(str(select.source_request_id))
    for step in spec.steps:
        if step.step_id in ids:
            continue
        if str((step.source_meta or {}).get("request_id") or "") in request_ids:
            ids.add(step.step_id)
            continue
        if _request_path({"url": step.path or step.url}) in urls:
            ids.add(step.step_id)
    return ids


def _read_status_steps(spec: FlowSpec) -> list[FlowStep]:
    out: list[FlowStep] = []
    for st in spec.steps:
        if _is_write_step(st):
            continue
        if _is_business_query_step(st):
            out.append(st)
    return out


def _ordered_steps_by_ids(spec: FlowSpec, ids: set[str]) -> list[FlowStep]:
    return [st for st in spec.steps if st.step_id in ids]


def _submit_capability_steps(spec: FlowSpec) -> list[FlowStep]:
    write_ids = {st.step_id for st in _write_steps(spec) if st.step_id}
    if not write_ids:
        return []
    option_source_ids = _option_source_step_ids(spec)
    # Option endpoints are data providers for SelectBinding/ParamField.  They
    # are not ordinary calls in the submit execution plan: adding them as a
    # preflight leaks their own filters (for example simple-list.status) into
    # the public submit contract and can make capability confirmation fail on
    # an enum that has nothing to do with the write.  A source that really
    # feeds a write through an explicit FlowLink is still retained by the
    # dependency closure rooted at ``write_ids`` below.
    dependency_ids = _dependency_closure_step_ids(spec, write_ids)
    preflight_ids = {
        st.step_id for st in spec.steps
        if bool((st.source_meta or {}).get("control_preflight_for_write"))
        and st.step_id not in option_source_ids
    }
    return _ordered_steps_by_ids(
        spec,
        _dependency_closure_step_ids(spec, dependency_ids | preflight_ids),
    )


def _capability_step_allowed(spec: FlowSpec, cap: FlowCapability, step: FlowStep) -> bool:
    role = (step.source_meta or {}).get("role") or step.semantic_role or ""
    kind = (cap.kind or "").strip()
    membership = next((ref for ref in (cap.request_refs or []) if ref.step_id == step.step_id), None)
    # Explicit user membership is authoritative. Request role describes evidence,
    # not whether the same request may execute inside a capability.
    if membership and membership.origin in {"manual", "user"} and membership.usage in {"execute", "preflight", "fact_check"}:
        return True
    if (
        membership
        and membership.origin == "compiler"
        and membership.usage in {"execute", "preflight"}
        and any(
            item.get("source") == "grounded_request_graph"
            for item in (cap.evidence or [])
            if isinstance(item, dict)
        )
    ):
        return True
    if step.step_id in set(_capability_scoped_step_ids(cap)) and (
        cap.updated_by == "user" or cap.locked or cap.confirmed or not role
    ):
        return True
    if kind == "query_status" and _is_business_query_step(step):
        return True
    if kind == "query_status" and (
        role == "read_option" or step.step_id in _option_source_step_ids(spec)
    ):
        return False
    method = (step.method or "GET").upper()
    if method in _WRITE_METHODS:
        return True
    if kind in WRITE_CAPABILITY_KINDS:
        closure_ids = {st.step_id for st in _submit_capability_steps(spec)}
        return step.step_id in closure_ids
    if kind == "query_status":
        status_ids = {st.step_id for st in _read_status_steps(spec)}
        return role != "read_option" and step.step_id in status_ids
    if kind == "list_options":
        return role == "read_option" or bool(step.selects)
    return role not in {"read_option", "read_context"}


def _query_operation_key(step: FlowStep) -> str:
    """Group all requests caused by one visible query command.

    The endpoint domain is not an operation boundary: one click can trigger a
    page query plus counters/statistics, while two different buttons in the
    same domain can represent independently callable query/detail operations.
    Locator identity is therefore used only for read capabilities and only
    when Observer proves a command anchor.
    """
    business = _capability_business_key(step)
    operation_kind = _capability_operation_kind(step)
    meta = step.source_meta or {}
    action_ref = str(
        meta.get("trigger_transaction_id") or meta.get("trigger_action_id") or ""
    ).strip()
    locator = re.sub(r"\s+", "", str(meta.get("trigger_locator") or "").casefold())
    anchored = bool(
        (action_ref or locator)
        and str(meta.get("trigger_op") or "").lower() in {"click", "submit"}
        and str(meta.get("causality_confidence") or "high").lower() in {"high", "medium"}
    )
    if not anchored:
        return (
            "__".join(part for part in (business, operation_kind) if part)
            if operation_kind != "query_status"
            else business
        )
    page_scope = str(
        meta.get("page_id") or meta.get("page_url") or meta.get("document_url") or ""
    )
    action_identity = "|".join((page_scope, action_ref, locator))
    action_key = hashlib.sha1(action_identity.encode("utf-8")).hexdigest()[:10]
    return f"action_{action_key}"


def _response_identity_match_count(step: FlowStep) -> int:
    """Count request identity leaves echoed by the captured response."""
    identity_keys = {
        "id", "recordid", "requestid", "applicationid", "businessid",
        "entityid", "itemid", "instanceid", "processinstanceid",
    }
    requested: dict[str, set[str]] = {}
    for param in step.params or []:
        leaf = re.split(r"[.\[\]]+", str(param.path or param.key or ""))[-1]
        key = re.sub(r"[^a-z0-9]+", "", leaf.casefold())
        if key not in identity_keys or param.value in (None, ""):
            continue
        values = param.value if isinstance(param.value, list) else [param.value]
        requested.setdefault(key, set()).update(
            str(value) for value in values if value not in (None, "")
        )
    if not requested:
        return 0

    matches: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = re.sub(r"[^a-z0-9]+", "", str(raw_key).casefold())
                if (
                    key in requested
                    and not isinstance(child, (dict, list))
                    and str(child) in requested[key]
                ):
                    matches.add(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(step.response_json)
    return len(matches)


def _primary_read_operation_step(steps: list[FlowStep]) -> FlowStep:
    """Choose the business result of one recorded command, not its first helper call."""
    if not steps:
        raise ValueError("read operation requires at least one step")

    def command_result_semantic_score(step: FlowStep) -> int:
        """Prefer the response shape named by the visible command."""
        meta = step.source_meta or {}
        command = " ".join((
            str(meta.get("trigger_locator") or ""),
            str(meta.get("trigger_op") or ""),
        )).casefold()
        families = (
            (
                r"(?:进度|流程|审批|节点|progress|workflow|process|approval|timeline)",
                (
                    "progress", "workflow", "process", "approval", "task",
                    "node", "activity", "bpmn", "timeline", "comment",
                    "finished", "unfinished", "rejected", "status",
                    "进度", "流程", "审批", "任务", "节点", "意见", "状态",
                ),
            ),
            (
                r"(?:评论|意见|备注|comment|opinion|remark)",
                ("comment", "opinion", "remark", "message", "评论", "意见", "备注"),
            ),
        )
        terms = next((values for pattern, values in families if re.search(pattern, command)), ())
        if not terms:
            return 0
        observed: list[str] = [str(step.path or step.url or "").casefold()]

        def collect_keys(value: Any, depth: int = 0) -> None:
            if depth > 4:
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    observed.append(str(key).casefold())
                    collect_keys(child, depth + 1)
            elif isinstance(value, list):
                for child in value[:3]:
                    collect_keys(child, depth + 1)

        collect_keys(step.response_json)
        return sum(
            1 for term in terms
            if any(term in value for value in observed)
        )

    def page_path_overlap(step: FlowStep) -> int:
        meta = step.source_meta or {}
        page_url = str(
            (meta.get("trigger_page_context") or {}).get("url")
            or meta.get("page_url")
            or meta.get("document_url")
            or ""
        )

        def segments(value: str) -> set[str]:
            return {
                segment
                for segment in re.split(
                    r"[^a-z0-9\u4e00-\u9fff]+",
                    urlparse(value).path.casefold(),
                )
                if segment
                and segment not in _CAPABILITY_PATH_PREFIXES
                and segment not in {"get", "list", "page", "detail", "query"}
                and len(segment) > 1
            }

        return len(
            segments(page_url) & segments(str(step.path or step.url or ""))
        )

    def score(step: FlowStep) -> tuple[int, int, int, int, int, int]:
        response = step.response_json
        payload = response.get("data") if isinstance(response, dict) else response
        entity_payload = int(isinstance(payload, dict) and not any(
            isinstance(payload.get(key), list)
            for key in ("list", "records", "rows", "items")
        ))
        role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
        return (
            command_result_semantic_score(step),
            _response_identity_match_count(step),
            page_path_overlap(step),
            entity_payload,
            int(role == "business_get"),
            _business_query_evidence_score(step),
        )

    return max(steps, key=score)


def _grounded_read_operation_steps(
    spec: FlowSpec, anchor: FlowStep,
) -> list[FlowStep]:
    """Return every captured read caused by the anchor's visible command."""
    operation_key = _query_operation_key(anchor)
    if not operation_key.startswith("action_"):
        return [anchor]
    members: list[FlowStep] = []
    for step in spec.steps:
        role = str((step.source_meta or {}).get("role") or step.semantic_role or "").lower()
        if (
            _is_write_step(step)
            or role in {"auth", "noise", "duplicate_observation"}
            or str((step.source_meta or {}).get("duplicate_observation_of") or "")
        ):
            continue
        if _query_operation_key(step) == operation_key:
            members.append(step)
    return members or [anchor]


def _step_page_id_from_facts(spec: FlowSpec, step: FlowStep) -> str:
    meta = step.source_meta or {}
    if meta.get("page_id"):
        return str(meta["page_id"])
    request_id = str(meta.get("request_id") or "")
    request_index = meta.get("request_index")
    for fact in spec.request_facts.requests or []:
        if request_id and str(fact.request_id or "") == request_id:
            return str(fact.page_id or "")
        if request_index is not None and fact.request_index == request_index:
            return str(fact.page_id or "")
    return ""


def _retired_capability_step_ids(spec: FlowSpec | None) -> set[str]:
    if spec is None:
        return set()
    removed = (spec.meta or {}).get("capability_removed_steps") or {}
    removed_step_ids = {
        str(ref).removeprefix("step:")
        for refs in removed.values()
        for ref in (refs or [])
        if str(ref).startswith("step:")
    }
    active_step_ids = {
        step_id
        for capability in (spec.capabilities or [])
        for step_id in _capability_node_step_ids(capability)
    }
    return removed_step_ids - active_step_ids


def _removed_capability_names(spec: FlowSpec | None) -> set[str]:
    if spec is None:
        return set()
    return {str(x) for x in ((spec.meta or {}).get("removed_capabilities") or []) if str(x)}


def _remember_removed_capability(spec: FlowSpec, cap_name: str, cap_kind: str = "") -> None:
    if not cap_name:
        return
    meta = dict(spec.meta or {})
    removed = set(str(x) for x in (meta.get("removed_capabilities") or []))
    removed.add(cap_name)
    meta["removed_capabilities"] = sorted(removed)
    if cap_kind:
        removed_kinds = set(str(x) for x in (meta.get("removed_capability_kinds") or []))
        removed_kinds.add(_capability_kind_family(cap_kind))
        meta["removed_capability_kinds"] = sorted(removed_kinds)
    spec.meta = meta


def _forget_removed_capability(spec: FlowSpec, cap_name: str, cap_kind: str = "") -> None:
    meta = dict(spec.meta or {})
    removed = [x for x in (meta.get("removed_capabilities") or []) if str(x) != cap_name]
    meta["removed_capabilities"] = removed
    if cap_kind:
        family = _capability_kind_family(cap_kind)
        meta["removed_capability_kinds"] = [
            x for x in (meta.get("removed_capability_kinds") or []) if str(x) != family
        ]
    spec.meta = meta


def _remember_removed_capability_step(spec: FlowSpec, cap_name: str, step_id: str) -> None:
    refs = sorted(_capability_step_ref_keys(spec, step_id))
    if not refs:
        return
    meta = dict(spec.meta or {})
    removed = {k: list(v or []) for k, v in (meta.get("capability_removed_steps") or {}).items()}
    cur = set(str(x) for x in removed.get(cap_name, []))
    cur.update(refs)
    removed[cap_name] = sorted(cur)
    meta["capability_removed_steps"] = removed
    spec.meta = meta


def _forget_removed_capability_step(spec: FlowSpec, cap_name: str, step_id: str) -> None:
    meta = dict(spec.meta or {})
    removed = {k: list(v or []) for k, v in (meta.get("capability_removed_steps") or {}).items()}
    if cap_name not in removed:
        return
    refs = _capability_step_ref_keys(spec, step_id)
    removed[cap_name] = [x for x in removed[cap_name] if x not in refs]
    if not removed[cap_name]:
        removed.pop(cap_name, None)
    meta["capability_removed_steps"] = removed
    spec.meta = meta


def _public_capability_anchor_step_ids(spec: FlowSpec) -> list[str]:
    """Derive the complete public action set from recorder-owned facts."""
    write_groups: dict[str, list[FlowStep]] = {}
    write_steps = [
        step for step in spec.steps
        if _is_write_step(step)
        and not str((step.source_meta or {}).get("duplicate_observation_of") or "")
        and str((step.source_meta or {}).get("role") or step.semantic_role or "").lower()
        not in {"auth", "noise", "read_context", "read_option", "option_source"}
    ]
    for step in write_steps:
        write_groups.setdefault(_write_operation_key(step), []).append(step)

    anchors = [steps[-1].step_id for steps in write_groups.values() if steps]
    submit_closure = {
        step.step_id for step in _submit_capability_steps(spec)
    } if write_steps else set()
    read_groups: dict[str, list[FlowStep]] = {}
    for step in _read_status_steps(spec):
        meta = step.source_meta or {}
        independently_triggered = _has_query_action_evidence(
            meta.get("trigger_op"), meta.get("trigger_locator"),
        )
        independently_grounded = _business_query_evidence_score(step) >= 5
        if (
            meta.get("record_hydration_for_write_ids")
            and not independently_triggered
            and not independently_grounded
        ):
            continue
        if step.step_id not in submit_closure or independently_triggered or independently_grounded:
            read_groups.setdefault(_query_operation_key(step), []).append(step)
    # One visible command is one public read ability even when it fans out to
    # record, workflow, user and statistics endpoints. Its full request group
    # is attached later by the Skill-submitted semantic plan.
    anchors.extend(
        _primary_read_operation_step(steps).step_id
        for steps in read_groups.values() if steps
    )
    order = {step.step_id: index for index, step in enumerate(spec.steps)}
    return sorted(dict.fromkeys(anchors), key=lambda step_id: order.get(step_id, 10**9))


def _active_capability_step_ids(spec: FlowSpec) -> set[str] | None:
    """返回当前对外能力实际使用的步骤。

    ``None`` 表示能力模型尚未建立，兼容旧 FlowSpec，仍按全部步骤处理；
    空集合表示能力模型已建立但当前没有能力（例如用户删除了全部能力），
    此时不能继续让已删除能力的字段、依赖和告警参与发布。
    """
    capability_model = (spec.meta or {}).get("capability_model") or {}
    if not spec.capabilities and not capability_model.get("status"):
        return None
    active: set[str] = set()
    for cap in spec.capabilities or []:
        active.update(_capability_node_step_ids(cap))
    return active


def _capability_child_nodes(node: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        child = node.get(key)
        if isinstance(child, list):
            return [n for n in child if isinstance(n, dict)]
    return []


def _capability_call_step_ids_from_nodes(nodes: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for node in _iter_capability_nodes(nodes):
        sid = str(node.get("step_id") or "")
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _capability_step_summary(step: FlowStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or step.url,
        "role": (step.source_meta or {}).get("role") or step.semantic_role,
        "request_id": (step.source_meta or {}).get("request_id"),
        "request_index": (step.source_meta or {}).get("request_index"),
    }


def _capability_node_step_ids(cap: FlowCapability) -> list[str]:
    return _capability_call_step_ids_from_nodes(cap.nodes or [])


def _step_request_key(step: FlowStep) -> str:
    meta = step.source_meta or {}
    if meta.get("request_id"):
        return f"id:{meta.get('request_id')}"
    if meta.get("request_index") is not None:
        return f"idx:{meta.get('request_index')}"
    return f"sig:{(step.method or '').upper()} {_request_path({'url': step.path or step.url})}"


def _capability_request_indexes(spec: FlowSpec) -> tuple[set[str], set[str]]:
    request_ids: set[str] = set()
    request_indexes: set[str] = set()
    for fact in (spec.request_facts.requests or []):
        if fact.request_id:
            request_ids.add(str(fact.request_id))
        if fact.request_index is not None:
            request_indexes.add(str(fact.request_index))
    for item in _request_fact_items(spec):
        if item.get("request_id"):
            request_ids.add(str(item.get("request_id")))
        if item.get("request_index") is not None:
            request_indexes.add(str(item.get("request_index")))
    return request_ids, request_indexes


def _ordered_capability_request_refs(refs: list[Any]) -> list[Any]:
    return sorted(
        list(refs or []),
        key=lambda ref: (
            _CAPABILITY_REF_USAGE_ORDER.get(str(getattr(ref, "usage", None) or "execute"), 9),
            getattr(ref, "sequence", None) if getattr(ref, "sequence", None) is not None else 10**9,
            str(getattr(ref, "path", "") or ""),
            str(getattr(ref, "step_id", "") or ""),
            str(getattr(ref, "request_id", "") or ""),
        ),
    )


def _attach_option_source_memberships(spec: FlowSpec) -> None:
    """Expose candidate-source ownership without executing it as a call node."""
    by_id = {step.step_id: step for step in spec.steps}
    by_path = {
        _request_path({"url": step.path or step.url}): step
        for step in spec.steps
    }
    facts = list((spec.request_facts or RequestFacts()).requests or [])
    facts_by_id = {str(fact.request_id or ""): fact for fact in facts if fact.request_id}
    node_ids_by_capability = {
        capability.capability_id: set(_capability_node_step_ids(capability))
        for capability in (spec.capabilities or [])
    }

    def captured_fact(source: dict[str, Any]) -> RequestFact | None:
        request_id = str(source.get("source_request_id") or "")
        if request_id and request_id in facts_by_id:
            return facts_by_id[request_id]
        source_path = _request_path({"url": str(source.get("source_url") or "")})
        if not source_path:
            return None
        # The latest duplicate capture represents the source selected most
        # recently by the operator.
        return next(
            (fact for fact in reversed(facts) if _request_path({"url": fact.path or fact.url}) == source_path),
            None,
        )

    for capability in spec.capabilities or []:
        node_ids = node_ids_by_capability.get(capability.capability_id, set())
        source_ids: set[str] = set()
        captured_source_facts: dict[str, RequestFact] = {}
        for step_id in node_ids:
            step = by_id.get(step_id)
            for param in (step.params if step else []):
                if param.source_kind != "api_option":
                    continue
                source = param.source or {}
                source_id = str(source.get("source_step_id") or "")
                if not source_id and source.get("source_url"):
                    source_step = by_path.get(_request_path({"url": str(source.get("source_url"))}))
                    source_id = source_step.step_id if source_step else ""
                if source_id in by_id and source_id not in node_ids:
                    source_ids.add(source_id)
                    continue
                fact = captured_fact(source)
                if fact is not None:
                    captured_source_facts[str(fact.request_id or fact.request_index or fact.path or fact.url)] = fact

        for source_id in source_ids:
            source_step = by_id[source_id]
            existing = next(
                (ref for ref in (capability.request_refs or []) if ref.step_id == source_id),
                None,
            )
            ref = _capability_request_ref_from_step(spec, source_step, existing)
            ref.usage = "option_source"
            ref.origin = "recorder"
            ref.confirmed = True
            capability.request_refs = [
                item for item in (capability.request_refs or []) if item.step_id != source_id
            ]
            capability.request_refs.append(ref)

        for fact in captured_source_facts.values():
            analysis = spec.request_facts.analysis.get(str(fact.request_id or ""))
            existing = next(
                (
                    ref for ref in (capability.request_refs or [])
                    if (fact.request_id and ref.request_id == fact.request_id)
                    or (
                        not ref.step_id
                        and _request_path({"url": ref.path}) == _request_path({"url": fact.path or fact.url})
                    )
                ),
                None,
            )
            ref = CapabilityRequestRef(
                request_id=str(fact.request_id or ""),
                request_index=fact.request_index,
                step_id="",
                role=(analysis.role if analysis else "") or "read_option",
                method=str(fact.method or "GET").upper(),
                path=fact.path or fact.url,
                sequence=fact.sequence,
                confidence=float((analysis.confidence if analysis else None) or 1.0),
                reason=(analysis.reason if analysis else "") or "字段候选来自录制捕获的只读接口",
                usage="option_source",
                origin="repair" if existing is None else existing.origin,
                confirmed=True,
            )
            capability.request_refs = [
                item for item in (capability.request_refs or [])
                if not (
                    (fact.request_id and item.request_id == fact.request_id)
                    or (
                        not item.step_id
                        and _request_path({"url": item.path}) == _request_path({"url": fact.path or fact.url})
                    )
                )
            ]
            capability.request_refs.append(ref)


def _canonical_step_summary(step: FlowStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or _request_path({"url": step.url}),
        "param_keys": [p.key or p.path for p in step.params],
        "param_types": {p.key or p.path: p.type for p in step.params},
        "select_count": len(step.selects or []),
        "response_hash": _stable_json_hash(step.response_json) if step.response_json is not None else "",
        "request_id": (step.source_meta or {}).get("request_id") or "",
        "request_index": (step.source_meta or {}).get("request_index"),
    }


def _capability_sequence_window(spec: FlowSpec, cap: FlowCapability) -> tuple[float | None, float | None]:
    by_id = {s.step_id: s for s in spec.steps}
    values = [
        seq for seq in (
            _step_sequence(by_id[sid])
            for sid in _capability_node_step_ids(cap)
            if sid in by_id
        )
        if seq is not None
    ]
    if not values:
        return None, None
    return min(values), max(values)

_PENDING_FLOW_SPEC_HELPERS = {'_CAPABILITY_PATH_PREFIXES': 'dano.execution.page.capability_kinds', '_CAPABILITY_REF_USAGE_ORDER': 'dano.execution.page.capability_contracts', '_business_query_evidence_score': 'dano.execution.page.capability_contracts', '_capability_business_key': 'dano.execution.page.capability_contracts', '_capability_kind_family': 'dano.execution.page.capability_kinds', '_capability_operation_kind': 'dano.execution.page.capability_kinds', '_capability_step_ref_keys': 'dano.execution.page.capability_contracts', '_is_business_query_step': 'dano.execution.page.capability_contracts', '_is_write_step': 'dano.execution.page.capability_kinds', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization', '_write_operation_key': 'dano.execution.page.capability_kinds', '_write_steps': 'dano.execution.page.capability_kinds', 'WRITE_CAPABILITY_KINDS': 'dano.execution.page.capability_kinds'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
