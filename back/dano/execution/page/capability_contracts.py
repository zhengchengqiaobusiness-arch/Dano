"""Stage 6: leftover capability helpers and facade re-exports."""
from __future__ import annotations

from typing import Any
import hashlib
import json
import re
from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.recording_facts import (
    _BUSINESS_QUERY_PATH_RE,
    _INTERNAL_WORKFLOW_READ_RE,
    _WRITE_METHODS,
    _has_query_action_evidence,
    _request_path,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _OPTION_SOURCE_KINDS,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _screenshot_control_evidence,
)
from dano.execution.page.flow_materialization.request_steps import (
    _infer_wire_format,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_exposed_to_caller,
)


















def _capability_field_from_param(
    step: FlowStep,
    param: ParamField,
    *,
    scope: str,
    request_id: str = "",
) -> CapabilityField:
    exposed = bool(param.exposed_to_user and param.category == "user_param")
    return CapabilityField(
        field_id=f"{scope}:{step.step_id}:{param.path}",
        scope=scope,
        display_name=param.label or param.key or param.path,
        path=param.path,
        key=param.key,
        type=param.type,
        wire_type=param.wire_type or _infer_type_from_value(param.value),
        wire_format=param.wire_format or _infer_wire_format(param.value),
        required=bool(param.required),
        request_id=request_id,
        request_index=(step.source_meta or {}).get("request_index"),
        step_id=step.step_id,
        source_kind=param.source_kind,
        source=dict(param.source or {}),
        category=param.category,
        enum_options=list(param.enum_options) if param.enum_options else None,
        enum_value_map=dict(param.enum_value_map) if param.enum_value_map else None,
        exposed_to_caller=exposed if scope != "request_field" else bool(param.exposed_to_user),
        confidence=float(param.confidence or 0.0),
        confirmed=bool(param.locked or not param.need_human_confirm),
        locked=bool(param.locked),
        evidence=list(param.evidence or []),
    )


def _capability_dependency_from_link(link: FlowLink) -> CapabilityDependency:
    dependency_id = link.link_id or hashlib.sha1(
        "|".join([link.source_step_id, link.source_path, link.target_step_id, link.target_path]).encode("utf-8")
    ).hexdigest()[:12]
    return CapabilityDependency(
        dependency_id=dependency_id,
        type="response_to_request",
        source={
            "step_id": link.source_step_id,
            "path": link.source_path,
            "tokens": link.source_tokens,
        },
        target={
            "step_id": link.target_step_id,
            "path": link.target_path,
            "tokens": link.target_tokens,
            "param_name": link.param_name,
        },
        confidence=float(link.confidence or 0.0),
        confirmed=bool(link.confirmed),
        locked=bool(link.locked),
        reason=link.reason,
        evidence=dict(link.evidence or {}),
    )




def _capability_dependency_merge_key(dep: CapabilityDependency) -> tuple[str, str, str, str]:
    source = dep.source or {}
    target = dep.target or {}
    return (
        str(source.get("step_id") or ""),
        _strip_body_prefix(str(source.get("path") or "")),
        str(target.get("step_id") or ""),
        _strip_body_prefix(str(target.get("path") or "")),
    )


def _merge_capability_scoped_dependencies(
    derived: list[CapabilityDependency],
    existing: list[CapabilityDependency],
) -> list[CapabilityDependency]:
    out = [item.model_copy(deep=True) for item in derived]
    by_key = {_capability_dependency_merge_key(item): idx for idx, item in enumerate(out)}
    by_id = {item.dependency_id: idx for idx, item in enumerate(out) if item.dependency_id}
    for item in existing or []:
        if not item.locked:
            continue
        copied = item.model_copy(deep=True)
        idx = by_id.get(copied.dependency_id)
        if idx is None:
            idx = by_key.get(_capability_dependency_merge_key(copied))
        if idx is None:
            out.append(copied)
            by_key[_capability_dependency_merge_key(copied)] = len(out) - 1
            if copied.dependency_id:
                by_id[copied.dependency_id] = len(out) - 1
        else:
            out[idx] = copied
    return out












































def _normalize_generated_capability_semantics(spec: FlowSpec, cap: FlowCapability) -> None:
    """Align Planner capabilities with the recorded request evidence before validation."""
    by_id = {step.step_id: step for step in spec.steps}
    steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
    writes = [step for step in steps if _is_write_step(step)]
    public_names = set(ALLOWED_CAPABILITY_KINDS)
    if cap.name in public_names and cap.kind in public_names and cap.name != cap.kind:
        cap.name = cap.kind
        if cap.kind == "submit" and "批量" in str(cap.title or ""):
            cap.title = str(cap.title).replace("批量", "", 1) or "提交"
        elif cap.kind == "submit_batch" and "批量" not in str(cap.title or ""):
            cap.title = "批量" + (str(cap.title or "提交"))
    duplicate_generated_name = bool(re.fullmatch(r"submit_batch\d+", str(cap.name or "")))
    needs_batch_audit = cap.kind in {"submit_batch", "validate_batch"}
    if cap.locked or (not cap.evidence and not duplicate_generated_name and not needs_batch_audit):
        return
    if not steps:
        return
    if cap.kind in {"submit", "submit_batch", "validate_batch"} and writes:
        actual_batch = _write_contract_is_batch(spec, writes, cap)
        if cap.kind == "submit_batch" and not actual_batch:
            cap.kind = "submit"
            if re.fullmatch(r"submit_batch\d*", str(cap.name or "")):
                cap.name = "submit"
            if "批量提交" in str(cap.title or ""):
                cap.title = str(cap.title).replace("批量提交", "提交")
            cap.intent = "调用方提供业务字段；Skill 按能力内接口顺序执行前置查询、依赖注入和最终提交。"
    if cap.kind == "query_status":
        status_ids = {step.step_id for step in _read_status_steps(spec)}
        for step_id in set(cap.step_ids) - status_ids:
            cap.nodes = _remove_capability_step_nodes(cap.nodes or [], step_id)
        _sync_capability_order(spec, cap)
    elif cap.kind == "list_options":
        # 下拉来源属于字段执行细节，不自动暴露成独立业务能力。
        cap.nodes = []
        _sync_capability_order(spec, cap)






def _param_path_leaf(path: str) -> str:
    tokens = [token for token in re.split(r"[.\[\]/]+", _strip_body_prefix(path or "")) if token]
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", tokens[-1].lower()) if tokens else ""


def _param_identity_values(param: ParamField, key: str) -> set[str]:
    values = {
        str(item.get(key) or "")
        for item in (param.evidence or [])
        if isinstance(item, dict) and item.get(key)
    }
    source_value = (param.source or {}).get(key)
    if source_value:
        values.add(str(source_value))
    return {value for value in values if value}


def _params_can_share_caller_key(left: ParamField, right: ParamField) -> bool:
    """Only explicit field/control identity may share one public caller key."""
    compatible_type = bool(
        _business_type_for_param(left) == _business_type_for_param(right)
        and (left.wire_type or _infer_type_from_value(left.value) or "string")
        == (right.wire_type or _infer_type_from_value(right.value) or "string")
    )
    if not compatible_type:
        return False
    if left is right or (left.field_id and left.field_id == right.field_id):
        return True
    def option_identity(param: ParamField) -> tuple[str, str, str, str]:
        source = param.source or {}
        if isinstance(source.get("option_source"), dict):
            source = source["option_source"]
        return (
            str(source.get("source_request_id") or ""),
            str(source.get("source_url") or ""),
            str(source.get("value_key") or ""),
            str(source.get("label_key") or ""),
        )
    left_option = option_identity(left)
    right_option = option_identity(right)
    if any(left_option) and left_option == right_option:
        return True
    return any(
        _param_identity_values(left, identity_key)
        & _param_identity_values(right, identity_key)
        for identity_key in (
            "field_identity_id",
            "occurrence_id",
            "caller_input_id",
            "shared_input_id",
        )
    )


def _caller_key_location(param: ParamField) -> str:
    path = str(param.path or "").casefold()
    for location in ("query", "header", "path", "body"):
        if path.startswith(f"{location}."):
            return location
    return "body"


def _caller_key_token(value: str, fallback: str) -> str:
    token = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", str(value or "")).strip("_")
    return token or fallback


def _contextual_caller_key(step: FlowStep, param: ParamField, base: str, *, rich: bool) -> str:
    location = _caller_key_location(param)
    if not rich:
        return f"{location}_{base}"
    route = _strip_body_prefix(str(step.path or step.url or "")).split("?", 1)[0]
    route_token = _caller_key_token("_".join(re.split(r"[/\[\].]+", route)[-2:]), "request")
    method = _caller_key_token(str(step.method or "request").casefold(), "request")
    return f"{method}_{route_token}_{location}_{base}"


def _apply_contextual_param_key(
    step: FlowStep,
    param: ParamField,
    *,
    base: str,
    candidate: str,
    changes: list[dict[str, Any]],
) -> None:
    if param.key == candidate:
        return
    old_key = param.key
    param.key = candidate
    param.source = {
        **(param.source or {}),
        "original_key": old_key or base,
        "collision_resolved": True,
        "name_disambiguation": "structural_context",
    }
    param.need_human_confirm = True
    param.evidence = [*(param.evidence or []), {
        "kind": "field_key_collision_resolved",
        "original_key": old_key or base,
        "resolved_key": candidate,
        "path": param.path,
        "step_id": step.step_id,
        "actor": "heuristic",
        "reason": "同名字段缺少共享 caller input 的强身份；使用请求结构上下文消歧",
    }]
    for binding in step.selects or []:
        if binding.path and _strip_body_prefix(binding.path) == _strip_body_prefix(param.path):
            binding.param = candidate
    changes.append({
        "step_id": step.step_id,
        "path": param.path,
        "original_key": old_key or base,
        "resolved_key": candidate,
    })


def _disambiguate_capability_param_keys(steps: list[FlowStep]) -> list[dict[str, Any]]:
    """Disambiguate same-name caller inputs from stable request structure."""
    entries = [(step, param) for step in steps for param in (step.params or []) if _param_exposed_to_caller(param)]
    for step, param in entries:
        source = dict(param.source or {})
        collision_evidence = next((
            item for item in reversed(param.evidence or [])
            if isinstance(item, dict)
            and item.get("kind") == "field_key_collision_resolved"
            and item.get("actor") == "heuristic"
            and str(item.get("resolved_key") or "") == str(param.key or "")
        ), {})
        original = str(
            source.get("original_key")
            or collision_evidence.get("original_key")
            or ""
        ).strip()
        if (
            not original
            or (
                source.get("collision_resolved") is not True
                and not collision_evidence
            )
            or param.locked
            or param.name_source == "manual"
        ):
            continue
        param.key = original
        param.source = {
            key: value for key, value in source.items()
            if key not in {"original_key", "collision_resolved", "name_disambiguation"}
        }
        if (param.source or {}).get("array_item_member") is True:
            param.source = {**(param.source or {}), "array_item_key": original}
        param.evidence = [
            item for item in (param.evidence or [])
            if not (
                isinstance(item, dict)
                and item.get("kind") == "field_key_collision_resolved"
                and item.get("actor") == "heuristic"
            )
        ]
        for binding in step.selects or []:
            if binding.path and _strip_body_prefix(binding.path) == _strip_body_prefix(param.path):
                binding.param = original
    def key_namespace(param: ParamField) -> str:
        source = param.source or {}
        if source.get("array_item_member") is True:
            return str(source.get("array_container_path") or "")
        return ""

    grouped: dict[tuple[str, str], list[tuple[FlowStep, ParamField]]] = {}
    for step, param in entries:
        key = str(param.key or param.path or "").strip() or "field"
        grouped.setdefault((key_namespace(param), key), []).append((step, param))
    used = set(grouped)
    changes: list[dict[str, Any]] = []
    for (namespace, base), same_name in grouped.items():
        clusters: list[list[tuple[FlowStep, ParamField]]] = []
        for entry in same_name:
            cluster = next((
                item for item in clusters
                if _params_can_share_caller_key(item[0][1], entry[1])
            ), None)
            if cluster is None:
                clusters.append([entry])
            else:
                cluster.append(entry)
        if len(clusters) < 2:
            continue
        locked_cluster = next((
            cluster for cluster in clusters
            if any(param.locked for _step, param in cluster)
        ), None)
        pending = [cluster for cluster in clusters if cluster is not locked_cluster]
        simple_keys = [
            _contextual_caller_key(cluster[0][0], cluster[0][1], base, rich=False)
            for cluster in pending
        ]
        for cluster, simple_key in zip(pending, simple_keys):
            candidate = simple_key
            if simple_keys.count(simple_key) > 1 or (namespace, candidate) in used:
                candidate = _contextual_caller_key(cluster[0][0], cluster[0][1], base, rich=True)
            if (namespace, candidate) in used:
                identity = "\0".join(
                    str(value or "")
                    for value in (
                        cluster[0][0].method,
                        cluster[0][0].url or cluster[0][0].path,
                        cluster[0][1].path,
                        cluster[0][0].source_meta.get("request_id"),
                    )
                )
                candidate = f"{candidate}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8]}"
            used.add((namespace, candidate))
            for step, param in cluster:
                if not param.locked:
                    _apply_contextual_param_key(
                        step,
                        param,
                        base=base,
                        candidate=candidate,
                        changes=changes,
                    )
    for step in steps:
        step.sample_inputs = {
            str(param.key or param.path): param.value
            for param in (step.params or [])
            if param.value not in (None, "")
            and param.source_kind != "dynamic_structure"
            and str((param.source or {}).get("kind") or "") != "dynamic_structure_leaf"
        }
    return changes


_ACTIONABLE_PLACEHOLDER_NAME_RE = re.compile(
    r"^(?:请输入|请选择|请填写|请选取|请录入)\s*[：:、，,。.!！?？-]*\s*(.+)$",
    re.I,
)


def _normalize_actionable_placeholder_param_names(spec: FlowSpec) -> list[dict[str, str]]:
    """Turn a uniquely recoverable placeholder into its business field name.

    ``请输入撤回原因`` carries enough page evidence to become ``撤回原因``;
    vague examples such as ``例如 XXX`` do not, and remain operator advice.
    Manual/locked names are never rewritten.
    """
    changes: list[dict[str, str]] = []
    for step in spec.steps:
        for param in step.params or []:
            current = str(param.key or param.label or "").strip()
            match = _ACTIONABLE_PLACEHOLDER_NAME_RE.fullmatch(current)
            if (
                not match
                or param.locked
                or param.name_source == "manual"
            ):
                continue
            business_name = re.sub(r"\s+", "", match.group(1)).strip("：:、，,。.!！?？-_ ")
            if not business_name or business_name == current:
                continue
            try:
                _rename_param_public_key(spec, step, param, business_name, actor="planner")
            except ValueError:
                # A duplicate business name is ambiguous; preserve both fields
                # and expose the normal structured warning instead.
                continue
            changes.append({
                "step_id": step.step_id,
                "path": param.path,
                "old_name": current,
                "new_name": business_name,
            })
    return changes






def _capability_page_ids(
    spec: FlowSpec,
    capability: FlowCapability,
    step_by_id: dict[str, FlowStep],
) -> set[str]:
    return {
        page_id
        for step_id in _capability_scoped_step_ids(capability)
        if step_id in step_by_id
        if (page_id := _step_page_id_from_facts(spec, step_by_id[step_id]))
    }




















_ROUTING_FIELD_RE = re.compile(
    r"(?:approv|assignee|reviewer|audit|leader|manager|hr|cc|copy|审批|审核|领导|人力|抄送|经办)",
    re.I,
)


def _capability_has_explicit_batch_intent(cap: FlowCapability) -> bool:
    """Only preserve a caller-visible batch contract when it has grounded intent."""
    if any(
        isinstance(item, dict)
        and any(bool(item.get(key)) for key in ("batch", "batch_intent", "repeated_submission"))
        for item in (cap.evidence or [])
    ):
        return True
    # A user-authored/locked foreach over input.entries is an explicit reusable
    # batch design. Planner-generated loops alone are not evidence.
    has_entries_loop = any(
        node.get("type") == "foreach"
        and str(node.get("items") or "") in {"input.entries", "entries"}
        for node in _iter_capability_nodes(cap.nodes or [])
    )
    if has_entries_loop and (cap.updated_by == "user" or cap.locked):
        return True
    schema_properties = dict((cap.input_schema or {}).get("properties") or {})
    if any(
        isinstance(schema_properties.get(name), dict)
        and schema_properties[name].get("x-dano-capability-owned") is True
        and schema_properties[name].get("x-dano-operator-owned") is True
        for name in ("entries", "items")
    ):
        return True
    if any(
        (field.key or field.path) in {"entries", "items"}
        # ``confirmed`` alone is not operator evidence: Planner patch ops can
        # emit confirmed fields.  Counting that as proof lets the Planner invent
        # entries and then use its own invention to keep a false submit_batch.
        and (field.locked or cap.updated_by == "user")
        for field in (cap.inputs or [])
    ):
        return True
    # Planner-created foreach/schema is a proposal, not evidence. It may only
    # become public batch behavior through recorded request shape/query evidence
    # or an explicit operator edit handled above.
    return False






def _title_without_step_suffix(title: str) -> str:
    text = str(title or "").strip()
    text = re.sub(r"\s*[\(（]\s*\d+\s*步\s*[\)）]\s*$", "", text)
    return text.strip()






def _flow_capability_id(kind: str, seed: str = "") -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", f"{kind}_{seed}".strip("_")).strip("_").lower()
    return raw[:64] or kind


def _stable_capability_id(name: str, kind: str, step_ids: list[str]) -> str:
    raw = json.dumps([name, kind, list(step_ids)], ensure_ascii=False, separators=(",", ":"))
    return f"cap_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"










def _mark_repeated_write_observations(spec: FlowSpec) -> None:
    """Keep repeated facts/steps for audit, but execute one reusable command."""
    representatives: dict[tuple[Any, ...], FlowStep] = {}
    for step in spec.steps:
        signature = _repeated_write_command_signature(step)
        if signature is None:
            continue
        meta = step.source_meta or {}
        representative = representatives.get(signature)
        if representative is None:
            representatives[signature] = step
            continue
        step.source_meta = {
            **meta,
            "role": "duplicate_observation",
            "duplicate_observation_of": representative.step_id,
        }
        for capability in spec.capabilities or []:
            member_ids = set(_capability_node_step_ids(capability))
            if step.step_id not in member_ids:
                continue
            if representative.step_id in member_ids:
                capability.nodes = _remove_capability_step_nodes(
                    capability.nodes, step.step_id,
                )
            else:
                def replace_duplicate(nodes: list[dict[str, Any]]) -> None:
                    for node in nodes or []:
                        if not isinstance(node, dict):
                            continue
                        for key in ("step_id", "from", "source"):
                            if str(node.get(key) or "") == step.step_id:
                                node[key] = representative.step_id
                        for child_key in ("children", "steps", "then", "else", "otherwise"):
                            if isinstance(node.get(child_key), list):
                                replace_duplicate(node[child_key])

                replace_duplicate(capability.nodes)
            _sync_capability_order(spec, capability)
        request_ids = [
            request_id
            for request_id, usage in spec.request_facts.usage.items()
            if usage.materialized_step_id == step.step_id
        ]
        for request_id in request_ids:
            analysis = spec.request_facts.analysis.get(request_id)
            if analysis is None:
                continue
            analysis.role = "duplicate_observation"
            analysis.keep = False
            analysis.reason = (
                f"同一页面命令与接口契约的重复录制，复用步骤 {representative.step_id}"
            )






def _business_query_evidence_score(step: FlowStep) -> int:
    if _is_write_step(step):
        return -100
    path = _request_path({"url": step.path or step.url}).lower()
    role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
    if role in {"read_option", "option_source", "explicit_read_option"}:
        return -10
    if _INTERNAL_WORKFLOW_READ_RE.search(path):
        return -10
    if role != "business_get" and (
        re.search(
            r"(?:tenant|dict(?:ionary)?|options?|simple-list|departments?|roles?)",
            path,
        )
        or re.search(r"(?:^|/)(?:system|im)/users?(?:/|$)", path)
    ):
        return -10
    # An accepted recording-agent business_get classification is already the
    # semantic evidence required by the public capability gate.  Requiring a
    # second URL/DOM heuristic made valid non-REST search endpoints disappear
    # after materialization even though Pi had explicitly approved them.
    score = 3 if role == "business_get" else 0
    if _has_query_action_evidence(
        (step.source_meta or {}).get("trigger_op"),
        (step.source_meta or {}).get("trigger_locator"),
    ):
        score += 4
    if _BUSINESS_QUERY_PATH_RE.search(path):
        score += 2
    response = step.response_json
    if isinstance(response, list):
        score += 4
    if isinstance(response, dict):
        payload = response.get("data", response)
        if (
            isinstance(payload, dict)
            and _response_identity_match_count(step) > 0
        ):
            # A GET keyed by a stable record identity that returns the same
            # entity is independently callable business evidence.  Opening an
            # edit form may be how it was captured, but that does not make the
            # read endpoint merely an internal write preflight.
            score += 2
        for candidate in ("data.list", "data.records", "data.rows", "data.items", "list", "records", "rows", "items"):
            value = _flow_path_lookup(response, candidate)
            if isinstance(value, list):
                score += 4
                break
        if any(_flow_path_lookup(response, candidate) is not _FLOW_PATH_MISSING for candidate in ("data.total", "total", "count")):
            score += 1
    return score


def _is_business_query_step(step: FlowStep) -> bool:
    return _business_query_evidence_score(step) >= 3


















def _capability_business_key(step: FlowStep) -> str:
    """Return a conservative business-domain key for automatic splitting.

    Explicit recorder/planner metadata wins. Otherwise retain the complete
    stable resource route and strip only infrastructure/version prefixes and
    terminal operation verbs.
    """
    from dano.execution.page.capability_kinds import _CAPABILITY_PATH_PREFIXES

    meta = step.source_meta or {}
    explicit = str(meta.get("capability_key") or meta.get("business_domain") or "").strip()
    if explicit:
        return _flow_capability_id("domain", explicit).removeprefix("domain_")
    path = _request_path({"url": step.path or step.url}).lower()
    segments = [segment for segment in path.split("/") if segment]
    while segments and (
        segments[0] in _CAPABILITY_PATH_PREFIXES
        or re.fullmatch(r"v\d+", segments[0])
        or segments[0] in {"api", "gateway", "service", "rest"}
    ):
        segments.pop(0)
    operation_tokens = {
        "add", "create", "save", "update", "edit", "delete", "remove",
        "approve", "reject", "revoke", "withdraw", "detail", "get", "list",
        "page", "search", "query", "export", "download", "preview", "validate",
        "submit", "status", "get-count", "count",
    }
    while segments and (
        segments[-1] in operation_tokens or re.fullmatch(r"\d+", segments[-1])
    ):
        segments.pop()
    stable = "/".join(segments)
    domain = _flow_capability_id("domain", stable).removeprefix("domain_") if stable else ""
    # Trigger evidence is useful for explaining and validating the chain, but
    # must not be a hard partition key. One business capability routinely has
    # several buttons (query/add/submit); hashing each locator fragmented it
    # into artificial capabilities and made the first split hard to recover.
    return domain












_FIELD_MAPPED_CAPABILITY_RELATIONS = {"external_transform", "data_mapping", "field_mapping"}


def _capability_relation_requires_fields(relation: CapabilityRelation) -> bool:
    relation_kind = str(relation.mode or relation.type or "").strip().lower()
    return relation_kind in _FIELD_MAPPED_CAPABILITY_RELATIONS




def _capability_relation_schemas_compatible(source: dict[str, Any], target: dict[str, Any]) -> bool:
    source_type = str(source.get("type") or "")
    target_type = str(target.get("type") or "")
    if not source_type or not target_type or not _capability_types_compatible(source_type, target_type):
        return False
    if source.get("type") == target.get("type") == "array":
        source_items = source.get("items") if isinstance(source.get("items"), dict) else {}
        target_items = target.get("items") if isinstance(target.get("items"), dict) else {}
        return _capability_relation_schemas_compatible(source_items, target_items)
    if source_type == target_type == "object":
        source_properties = source.get("properties") if isinstance(source.get("properties"), dict) else {}
        target_properties = target.get("properties") if isinstance(target.get("properties"), dict) else {}
        required = {str(item) for item in target.get("required") or []}
        if not required.issubset(source_properties):
            return False
        return all(
            isinstance(source_properties.get(name), dict)
            and isinstance(target_properties.get(name), dict)
            and _capability_relation_schemas_compatible(
                source_properties[name], target_properties[name],
            )
            for name in required
        )
    return True


def _ensure_external_transform_relations(spec: FlowSpec) -> FlowSpec:
    """Describe grounded caller-owned capability cooperation without auto-running it."""
    spec.capability_relations = [
        _normalize_capability_relation_semantics(relation)
        for relation in (spec.capability_relations or [])
    ]
    capability_by_ref = {
        ref: cap
        for cap in spec.capabilities
        for ref in (cap.name, cap.capability_id)
        if ref
    }
    def relation_is_valid(relation: CapabilityRelation) -> bool:
        source = capability_by_ref.get(relation.from_capability)
        target = capability_by_ref.get(relation.to_capability)
        evidence_kind = str((relation.evidence or {}).get("kind") or "").strip().lower()
        if evidence_kind in {"user_confirmed", "manual", "manual_relation"}:
            return True
        if not _capability_relation_requires_fields(relation):
            return True
        if source is None or target is None:
            return bool(relation.confirmed and evidence_kind != "typed_capability_contract")
        source_field = _schema_node_at_path(source.output_schema, relation.from_output)
        target_field = _schema_node_at_path(target.input_schema, relation.to_input)
        if not (
            relation.from_output
            and relation.to_input
            and isinstance(source_field, dict)
            and isinstance(target_field, dict)
        ):
            return bool(relation.confirmed and evidence_kind != "typed_capability_contract")
        if evidence_kind == "typed_capability_contract":
            return _capability_relation_schemas_compatible(source_field, target_field)
        # Keep an explicit, resolvable relation so the validation report can
        # surface a type mismatch instead of silently deleting user intent.
        return True

    spec.capability_relations = [
        relation for relation in spec.capability_relations if relation_is_valid(relation)
    ]
    deduped_relations: list[CapabilityRelation] = []
    seen_relations: set[tuple[str, str, str, str, str]] = set()
    for relation in spec.capability_relations:
        identity = (
            relation.from_capability, relation.from_output,
            relation.to_capability, relation.to_input,
            str(relation.mode or relation.type or ""),
        )
        if identity in seen_relations:
            continue
        seen_relations.add(identity)
        deduped_relations.append(relation)
    spec.capability_relations = deduped_relations
    return spec






def _capability_step_ref_keys(spec: FlowSpec | None, step_id: str) -> set[str]:
    refs = {f"step:{step_id}"}
    if spec is not None:
        step = next((s for s in spec.steps if s.step_id == step_id), None)
        if step is not None:
            signature = _step_request_signature_key(step)
            if signature:
                refs.add(f"sig:{signature}")
    return refs


def _capability_removed_step_refs(spec: FlowSpec | None, cap_name: str) -> set[str]:
    if spec is None:
        return set()
    removed = ((spec.meta or {}).get("capability_removed_steps") or {}).get(cap_name) or []
    return {str(x) for x in removed if str(x)}










def _capability_step_was_removed(spec: FlowSpec | None, cap_name: str, step_id: str) -> bool:
    removed = _capability_removed_step_refs(spec, cap_name)
    if not removed:
        return False
    return bool(_capability_step_ref_keys(spec, step_id) & removed)








def _planned_capability_has_public_anchor(
    spec: FlowSpec,
    kind: str,
    planned_step_ids: list[str],
) -> bool:
    """Only user-callable business actions may create public capabilities."""
    by_id = {step.step_id: step for step in spec.steps}
    option_ids = _option_source_step_ids(spec)
    for step_id in planned_step_ids:
        step = by_id.get(step_id)
        if step is None:
            continue
        # `/list` is common to both business searches and option endpoints.
        # Strong recorded business-query evidence wins over the URL heuristic.
        if step_id in option_ids and kind in READ_CAPABILITY_KINDS:
            recorded_role = str(
                (step.source_meta or {}).get("role") or step.semantic_role or ""
            )
            if recorded_role != "business_get":
                continue
        grounded_kind = _capability_operation_kind(step)
        if kind in WRITE_CAPABILITY_KINDS and grounded_kind in WRITE_CAPABILITY_KINDS:
            return True
        if kind in READ_CAPABILITY_KINDS and grounded_kind in READ_CAPABILITY_KINDS and _is_business_query_step(step):
            return True
        action_text = " ".join(str((step.source_meta or {}).get(key) or "") for key in (
            "trigger_locator", "trigger_op", "reason",
        )) + " " + str(step.path or step.url or "")
        recorded_role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
        if (
            kind in READ_CAPABILITY_KINDS
            and recorded_role in {"business_get", "read_context"}
            and re.search(r"导出|下载|预览|校验|export|download|preview|validate", action_text, re.I)
        ):
            return True
    return False








def _is_technical_business_title(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[\s_-]+", "", text.lower())
    endpoint_action = re.search(
        r"(?:^|[/_.-])(?:get|list|page|query|search|submit|save|create|update|delete|"
        r"cancel|withdraw|approve|reject|start|process)(?:[/_.-]|$)",
        text,
        re.I,
    )
    endpoint_flow_title = bool(
        re.search(r"[A-Za-z]", text)
        and (
            endpoint_action
            or re.search(r"[/_-]", text)
            or re.search(r"[a-z][A-Z]", text)
        )
        and re.search(r"流程\s*[（(]?\s*\d*\s*步?", text)
    )
    return bool(
        endpoint_flow_title
        or re.search(
            r"(?:查询|提交|执行|处理)?\s*(?:get|post|put|patch|delete|cancel|withdraw)"
            r"(?:[-_/]|[A-Z])",
            text,
            re.I,
        )
        or re.match(r"^(?:get|post|put|patch|delete)", normalized)
        or normalized in {
            "流程", "业务流程", "提交流程", "submit", "submitprocess",
            "录制业务", "录制业务流程", "提交业务申请", "查询流程状态", "未命名",
        }
        or re.fullmatch(r"(?:capability|能力)\d*", normalized)
        or re.fullmatch(r"submitprocess流程(?:\(\d+步\))?", normalized)
        or bool(re.fullmatch(r"(?:action|sk)[_-]?[0-9a-f]{8,}", text, re.I))
    )


_GENERIC_PAGE_TITLE_RE = re.compile(
    r"^(?:OA\s*)?(?:管理)?(?:平台|系统|工作台|首页|业务平台|办公平台|管理系统)$|"
    r"^(?:申请|查询|搜索|筛选|基本|详细|更多)?信息$|^(?:申请|查询|搜索)条件$|"
    r"^(?:确定|取消|关闭|新增|编辑|详情|操作|撤回成功|提交成功)$",
    re.I,
)


def _clean_page_business_candidate(value: Any) -> str:
    """Normalize one visible heading without guessing a business domain."""
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -_|—·>/»›")
    if not text:
        return ""
    # Breadcrumb containers are sometimes captured as one string.  Preserve
    # the terminal business crumb and discard navigation chrome.
    chunks = [part.strip() for part in re.split(r"\s*(?:/|>|»|›|→|\||—| - )\s*", text) if part.strip()]
    if chunks:
        text = chunks[-1]
    for prefix in ("当前位置", "系统首页", "管理首页", "工作台首页", "首页"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):].strip(" -_|—·>/»›")
    text = re.sub(r"\s*[（(]\s*\d+\s*[）)]\s*$", "", text).strip()
    if not text or len(text) > 40 or _GENERIC_PAGE_TITLE_RE.fullmatch(text):
        return ""
    if re.search(r"(?:管理平台|管理系统|业务平台|办公平台)$", text):
        text = re.sub(r"(?:管理平台|管理系统|业务平台|办公平台)$", "", text).strip()
        if not text:
            return ""
    if _is_technical_business_title(text):
        return ""
    return text


def _page_context_business_name_from_contexts(contexts: list[dict[str, Any]]) -> str:
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for context in contexts:
        if not isinstance(context, dict):
            continue
        document_title = str(context.get("document_title") or "").strip()
        candidates = [*(context.get("visible_titles") or []), document_title]
        for position, raw in enumerate(candidates):
            text = _clean_page_business_candidate(raw)
            if not text or text in seen:
                continue
            seen.add(text)
            score = 0
            if raw == document_title:
                score += 3
            if 2 <= len(text) <= 20:
                score += 2
            if re.search(r"[\u4e00-\u9fff]", text):
                score += 1
            if re.search(r"管理|平台|系统|首页|工作台", text):
                score -= 4
            ranked.append((score, -position, text))
    best = max(ranked, default=(0, 0, ""))
    return best[2] if best[0] > 0 else ""


def _capability_text_is_placeholder(value: str, capability: FlowCapability) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[\s_-]+", "", text.casefold())
    capability_name = re.sub(r"[\s_-]+", "", str(capability.name or "").casefold())
    return bool(
        normalized == capability_name
        or re.fullmatch(r"(?:capability|能力)\d*", normalized)
        or _is_technical_business_title(text)
    )


_GENERIC_CAPABILITY_INTENT_RE = re.compile(
    r"(?:查询流程、审批或上下文详情|调用方提供业务字段|"
    r"按录制顺序调用\s*\d+\s*个真实接口|根据调用方提供的条件调用已录制查询接口|"
    r"按已纳入接口顺序执行前置查询)",
)


def _capability_intent_needs_refresh(value: str, capability: FlowCapability) -> bool:
    text = str(value or "").strip()
    return bool(
        not text
        or text == str(capability.title or "").strip()
        or _is_technical_business_title(text)
        or _GENERIC_CAPABILITY_INTENT_RE.search(text)
    )


def _locator_action_name(locator: str) -> str:
    text = str(locator or "").strip()
    match = re.search(r"\[name=([^\]]+)\]", text)
    if match:
        return match.group(1).strip(" '\"")
    text_matches = re.findall(r"(?:^|[\s>])text=([^\s>\]]+)", text)
    if text_matches:
        return text_matches[-1].strip(" '\"")
    if text.startswith("text="):
        return text[5:].strip(" '\"")
    if "=" in text:
        prefix, value = text.split("=", 1)
        if prefix.strip().lower() in {"button", "role", "label", "name"}:
            return value.strip(" '\"")
    return next((label for label in _ACTION_LABELS if label in text), "")






_INSTANCE_TITLE_SUFFIX_RE = re.compile(
    r"\s*[\(（]\s*(?:ID|id|编号|单号|No\.?|NO)\s*[:：#]?\s*[^)）]+[\)）]\s*$"
)


def _generalize_capability_title(title: str) -> str:
    """Drop recorded row samples from public capability titles."""
    return _INSTANCE_TITLE_SUFFIX_RE.sub("", str(title or "")).strip()


def _ensure_capability_explanations(
    spec: FlowSpec,
    semantic_plan: dict[str, Any] | None = None,
) -> FlowSpec:
    """Copy Skill-authored copy onto compiled capabilities; do not invent it."""
    plan_items = [
        item for item in ((semantic_plan or {}).get("capabilities") or [])
        if isinstance(item, dict)
    ]
    plan_by_name = {
        str(item.get("name") or ""): item for item in plan_items
        if str(item.get("name") or "")
    }

    def planned_for(capability: FlowCapability) -> dict[str, Any]:
        exact = plan_by_name.get(capability.name)
        if exact is not None:
            return exact
        cap_steps = set(_capability_node_step_ids(capability))
        scored = [
            (
                len(cap_steps & {
                    str(ref.get("step_id") or "")
                    for ref in (item.get("request_refs") or [])
                    if isinstance(ref, dict)
                }),
                item,
            )
            for item in plan_items
        ]
        if not scored:
            return {}
        top_score = max(score for score, _item in scored)
        if top_score <= 0:
            return {}
        top = [item for score, item in scored if score == top_score]
        return top[0] if len(top) == 1 else {}

    for capability in spec.capabilities or []:
        if capability.locked or capability.updated_by == "user":
            continue
        planned = planned_for(capability)
        planned_title = str(planned.get("title") or "").strip()
        if _capability_text_is_placeholder(capability.title, capability):
            capability.title = (
                _generalize_capability_title(planned_title)
                if planned_title and not _capability_text_is_placeholder(planned_title, capability)
                else (capability.name or capability.kind)
            )
        else:
            capability.title = _generalize_capability_title(capability.title) or capability.title
        planned_intent = str(planned.get("intent") or planned.get("description") or "").strip()
        if _capability_intent_needs_refresh(capability.intent, capability):
            capability.intent = planned_intent or capability.title or capability.name
    return spec


def _page_context_business_name(spec: FlowSpec) -> str:
    contexts = [dict((spec.meta or {}).get("page_context") or {})]
    for step in spec.steps or []:
        meta = step.source_meta or {}
        for key in ("trigger_page_context", "page_context"):
            value = meta.get(key)
            if isinstance(value, dict) and value:
                contexts.append(dict(value))
    return _page_context_business_name_from_contexts(contexts)






























def _capability_is_batch(spec: FlowSpec, cap: FlowCapability) -> bool:
    if cap.kind not in {"submit_batch", "validate_batch"}:
        return False
    by_id = {s.step_id: s for s in spec.steps}
    cap_steps = [by_id[sid] for sid in _capability_node_step_ids(cap) if sid in by_id]
    write_steps = [step for step in cap_steps if _is_write_step(step)]
    return _write_contract_is_batch(spec, write_steps, cap)




def _capability_field_summary(field: CapabilityField) -> dict[str, Any]:
    return {
        "field_id": field.field_id,
        "scope": field.scope,
        "display_name": field.display_name,
        "key": field.key,
        "path": field.path,
        "type": field.type,
        "required": bool(field.required),
        "step_id": field.step_id,
        "request_id": field.request_id,
        "request_index": field.request_index,
        "source_kind": field.source_kind,
        "exposed_to_caller": bool(field.exposed_to_caller),
        "confidence": float(field.confidence or 0.0),
        "confirmed": bool(field.confirmed),
        "locked": bool(field.locked),
    }


def _capability_dependency_summary(dep: CapabilityDependency) -> dict[str, Any]:
    return {
        "dependency_id": dep.dependency_id,
        "type": dep.type,
        "source": dict(dep.source or {}),
        "target": dict(dep.target or {}),
        "confidence": float(dep.confidence or 0.0),
        "confirmed": bool(dep.confirmed),
        "locked": bool(dep.locked),
        "reason": dep.reason,
    }


















def _only_grounded_screenshot_query_params_added(
    before: FlowSpec,
    candidate: FlowSpec,
) -> bool:
    before_steps = {step.step_id: step for step in before.steps}
    candidate_steps = {step.step_id: step for step in candidate.steps}
    if before_steps.keys() != candidate_steps.keys():
        return False
    added = 0
    for step_id, old_step in before_steps.items():
        new_step = candidate_steps[step_id]
        if (
            (old_step.method or "GET").upper() != (new_step.method or "GET").upper()
            or (old_step.path or old_step.url) != (new_step.path or new_step.url)
            or old_step.content_type != new_step.content_type
        ):
            return False
        old_params = {param.path: param for param in old_step.params}
        new_params = {param.path: param for param in new_step.params}
        if not old_params.keys() <= new_params.keys():
            return False
        if any(
            (old_params[path].wire_type or "") != (new_params[path].wire_type or "")
            for path in old_params
        ):
            return False
        for path in new_params.keys() - old_params.keys():
            param = new_params[path]
            if (
                (new_step.method or "GET").upper() not in {"GET", "HEAD"}
                or not path.startswith("query.")
                or _screenshot_control_evidence({"evidence": param.evidence}) is None
                or not any(
                    isinstance(item, dict)
                    and item.get("source") == "response_schema_match"
                    for item in param.evidence
                )
            ):
                return False
            added += 1
    return added > 0










def _step_request_signature_key(step: FlowStep) -> str:
    from dano.execution.page.request_identity import request_composite_signature

    return request_composite_signature({
        **(step.source_meta or {}),
        "method": step.method,
        "url": step.path or step.url,
        "body": step.body_source,
    })


def _eligible_business_write_fact(entry: dict[str, Any]) -> bool:
    return bool(
        entry.get("keep")
        and str(entry.get("role") or "") in {"business_write", "submit_anchor"}
        and str(entry.get("method") or "").upper() in _WRITE_METHODS
        and str(entry.get("path") or entry.get("url") or "").strip()
    )


def _capability_ref_key(value: Any) -> str:
    return str(value or "").strip()






def _capability_field_type(cap: FlowCapability, field_name: str, *, direction: str) -> str:
    field_name = _capability_ref_key(field_name)
    fields = cap.outputs if direction == "output" else cap.inputs
    for field in fields or []:
        if field_name in {field.path, field.key, field.display_name, field.field_id}:
            return str(field.type or "")
    schema = cap.output_schema if direction == "output" else cap.input_schema
    schema_type = _capability_schema_field_type(schema, field_name)
    if schema_type:
        return schema_type
    if direction == "output":
        for mapping in cap.output_mapping or []:
            if not isinstance(mapping, dict):
                continue
            names = {
                str(mapping.get("name") or ""),
                str(mapping.get("field") or ""),
                str(mapping.get("response_path") or ""),
                str(mapping.get("path") or ""),
            }
            if field_name and field_name in names:
                return "object" if field_name in {"response", "raw", "detail"} else "string"
    return ""


def _capability_types_compatible(source_type: str, target_type: str) -> bool:
    source = (source_type or "unknown").lower()
    target = (target_type or "unknown").lower()
    if not source or not target or "unknown" in {source, target}:
        return True
    aliases = {
        "integer": "number",
        "float": "number",
        "double": "number",
        "enum": "string",
        "list-enum": "array",
    }
    source = aliases.get(source, source)
    target = aliases.get(target, target)
    if source == target:
        return True
    if target == "string":
        return source in {"number", "boolean", "date", "datetime"}
    if target == "object":
        return True
    return False


def _step_body_is_array(step: FlowStep) -> bool:
    raw = str(step.body_source or "").strip()
    if not raw:
        return False
    try:
        return isinstance(json.loads(raw), list)
    except Exception:  # noqa: BLE001
        return raw.startswith("[")




def _capability_step_param_exists(step: FlowStep | None, path: str) -> bool:
    if step is None:
        return False
    normalized = _strip_body_prefix(path)
    for param in step.params or []:
        if path in {param.path, param.key, param.label} or normalized in {param.path, param.key, param.label}:
            return True
    return False




def _capability_field_looks_internal(field: CapabilityField) -> bool:
    text = f"{field.path}.{field.key}.{field.display_name}"
    if not _INTERNAL_EXPOSED_PATH_RE.search(text):
        return False
    source_kind = str(field.source_kind or "")
    if (
        source_kind in _OPTION_SOURCE_KINDS
        or source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
        or bool(field.enum_options or field.enum_value_map)
    ):
        return False
    return True


def _capability_execute_record_selector(cap: FlowCapability, field: CapabilityField) -> bool:
    """Record-bound commands may expose one abstract selected-record identity."""
    source_kind = str(field.source_kind or "")
    if (
        str(cap.kind or "") not in _MUTATING_RECORD_KINDS | {"inspect", "query_status"}
        or source_kind not in {"selected_record_identity", "selected_entity_id", "user_input"}
    ):
        return False
    text = f"{field.path}.{field.key}"
    return bool(re.search(r"(^|[.\]])(id|ids)(\]|$)", text, re.I))




def _capability_response_path_exists(step: FlowStep | None, path: str) -> bool:
    if step is None or step.response_json is None:
        return True
    normalized = _strip_body_prefix(path)
    if normalized in {"", "response", "$", "."}:
        return True
    return _flow_path_lookup(step.response_json, normalized) is not _FLOW_PATH_MISSING


def _capability_input_refs(expr: str) -> set[str]:
    refs = set(re.findall(r"\binput\.([a-zA-Z_][\w]*)", expr or ""))
    if re.fullmatch(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?\s*(?:==|!=|>=|<=|>|<|in\b).+", expr or ""):
        head = re.split(r"==|!=|>=|<=|>|<|\bin\b", expr, 1)[0].strip()
        if head and not head.startswith(("var.", "node.", "response.")):
            refs.add(head.split(".", 1)[0].removeprefix("input."))
    return {ref for ref in refs if ref}


def _capability_value_ref_exists(
    ref: str,
    *,
    input_props: dict[str, Any],
    cap_node_ids: set[str],
    step_by_id: dict[str, FlowStep],
    cap_step_id_set: set[str],
) -> bool:
    value = str(ref or "").strip()
    if not value:
        return False
    if (
        (len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'})
        or re.fullmatch(r"-?\d+(?:\.\d+)?", value)
        or value.lower() in {"true", "false", "null", "none"}
        or value.startswith(("literal:", "const:", "computed:"))
    ):
        return True
    if value.startswith("input."):
        return value.split(".", 1)[1].split(".", 1)[0] in input_props
    if value.startswith(("var.", "computed.", "loop.", "item.", "const.")):
        return True
    if value.startswith("node."):
        return value.split(".", 1)[1].split(".", 1)[0] in cap_node_ids
    if "." in value:
        head, tail = value.split(".", 1)
        if head in cap_node_ids:
            return True
        if head in cap_step_id_set:
            return _capability_response_path_exists(step_by_id.get(head), tail)
    return value in input_props or value in cap_node_ids or value in cap_step_id_set






def _capability_field_has_valid_source(
    field: CapabilityField,
    dependency_targets: set[tuple[str, str]],
) -> bool:
    if field.exposed_to_caller:
        return True
    if field.source:
        return True
    if field.source_kind and field.source_kind not in {"unknown", "user_input"}:
        return True
    return (field.step_id, _strip_body_prefix(field.path or field.key)) in dependency_targets








def _find_capability_by_ref(spec: FlowSpec, capability: str | FlowCapability) -> FlowCapability | None:
    if isinstance(capability, FlowCapability):
        return capability
    ref = str(capability or "").strip()
    if not ref:
        return None
    for cap in spec.capabilities or []:
        if ref in {cap.name, cap.capability_id, cap.title}:
            return cap
    return None






_PUBLIC_SOURCE_BY_INTERNAL = {
    "user_input": "caller_input",
    "api_option": "caller_input",
    "page_enum": "caller_input",
    "static_enum": "caller_input",
    "manual_enum": "caller_input",
    "form_option": "caller_input",
    "constant": "constant",
    "page_default": "caller_input",
    "page_rule": "constant",
    "request_header": "session",
    "current_user": "session",
    "storage": "session",
    "cookie": "session",
    "page_context": "context",
    "previous_response": "response_binding",
    "dynamic_structure": "response_binding",
    "selected_option_field": "computed",
    "computed": "computed",
    "system_time": "generated",
    "system_generated": "generated",
    "generated": "generated",
    "unknown": "unknown",
}


_CAPABILITY_REF_USAGE_ORDER = {
    "option_source": 0,
    "preflight": 1,
    "execute": 2,
    "fact_check": 3,
}


_PUBLIC_ROLE_BY_INTERNAL = {
    "auth": "auth",
    "noise": "support",
    "telemetry": "support",
    "unsupported_upload": "support",
    "unsupported_graphql": "support",
    "read_option": "option",
    "option_source": "option",
    "explicit_read_option": "option",
    "read_context": "context",
    "business_get": "business_read",
    "business_write": "business_write",
    "submit_anchor": "business_write",
}






def _find_capability_index(spec: FlowSpec, edit: dict[str, Any]) -> int:
    if "capability_index" in edit:
        idx = int(edit.get("capability_index"))
        if 0 <= idx < len(spec.capabilities):
            return idx
        raise ValueError(f"capability index out of range: {idx}")
    name = str(edit.get("capability_name") or edit.get("name") or "")
    if name:
        for idx, cap in enumerate(spec.capabilities):
            if cap.name == name:
                return idx
    raise ValueError("capability not found")










def _same_capability_computed_field(a: CapabilityField, b: CapabilityField) -> bool:
    """Match the only capability fields that remain authoritative at this level."""
    if a.field_id and b.field_id and a.field_id == b.field_id:
        return True
    if a.step_id or b.step_id:
        return False
    a_name = str(a.key or a.path or "").strip()
    b_name = str(b.key or b.path or "").strip()
    return bool(a_name and b_name and a_name == b_name)






def _upsert_global_link_from_capability_dependency(spec: FlowSpec, dep: CapabilityDependency) -> None:
    source = dep.source or {}
    target = dep.target or {}
    source_step_id = str(source.get("step_id") or "")
    target_step_id = str(target.get("step_id") or "")
    source_path = str(source.get("path") or "")
    target_path = str(target.get("path") or "")
    if not all([source_step_id, target_step_id, source_path, target_path]):
        return
    _find_step(spec, source_step_id)
    _find_step(spec, target_step_id)
    for link in spec.links:
        if (
            link.source_step_id == source_step_id
            and _strip_body_prefix(link.source_path) == _strip_body_prefix(source_path)
            and link.target_step_id == target_step_id
            and _strip_body_prefix(link.target_path) == _strip_body_prefix(target_path)
        ):
            link.confirmed = bool(dep.confirmed or link.confirmed)
            link.confidence = max(float(link.confidence or 0), float(dep.confidence or 0))
            link.reason = dep.reason or link.reason
            link.locked = bool(dep.locked or link.locked)
            return
    spec.links.append(FlowLink(
        source_step_id=source_step_id,
        source_path=source_path,
        target_step_id=target_step_id,
        target_path=target_path,
        confirmed=bool(dep.confirmed),
        confidence=float(dep.confidence or 0.75),
        reason=dep.reason or "能力级修复绑定的上游响应依赖",
        evidence=dep.evidence or {"source": "capability_dependency"},
        locked=bool(dep.locked),
    ))






_CAPABILITY_ALLOWED_FIELDS = frozenset({
    "name", "title", "intent", "kind", "capability_id", "request_refs", "step_ids", "fields",
    "inputs", "request_fields", "internal_fields", "computed_fields", "outputs", "dependencies",
    "input_schema", "output_schema",
    "output_mapping", "preconditions", "confirmed", "confidence",
    "requires_human_confirm", "evidence", "caller_responsibilities", "skill_responsibilities",
    "nodes", "status", "locked", "updated_by",
})

_PENDING_FLOW_SPEC_HELPERS = {'_AUTOMATED_FIELD_EDIT_ACTORS': 'dano.execution.page.flow_spec_core.controlled_edits', '_FLOW_PATH_MISSING': 'dano.execution.page.flow_spec_core.normalization', '_INTERNAL_EXPOSED_PATH_RE': 'dano.execution.page.flow_release', '_apply_link_sources': 'dano.execution.page.flow_spec_core.controlled_edits', '_apply_mechanical_field_contracts': 'dano.execution.page.flow_materialization.builder', '_client_redact_sensitive': 'dano.execution.page.flow_client_projection', '_enum_map_covers_recorded_value': 'dano.execution.page.flow_release', '_enum_options_look_value_only': 'dano.execution.page.flow_release', '_find_param': 'dano.execution.page.flow_spec_core.controlled_edits', '_find_step': 'dano.execution.page.flow_spec_core.controlled_edits', '_flow_path_lookup': 'dano.execution.page.flow_spec_core.normalization', '_incomplete_page_enum_is_executable': 'dano.execution.page.flow_release', '_infer_type_from_value': 'dano.execution.page.flow_spec_core.normalization', '_manual_enum_mapping_complete': 'dano.execution.page.flow_release', '_rename_param_public_key': 'dano.execution.page.flow_spec_core.controlled_edits', '_reset_param_source': 'dano.execution.page.flow_spec_core.controlled_edits', '_resolve_param_reference': 'dano.execution.page.flow_spec_core.controlled_edits', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization', '_transition_param_type': 'dano.execution.page.flow_spec_core.controlled_edits', 'append_flow_version': 'dano.execution.page.flow_spec_core.versioning', 'apply_flow_edits': 'dano.execution.page.flow_spec_core.controlled_edits', 'dry_run_flow_spec': 'dano.execution.page.flow_spec_core.request_contract', 'ensure_recorded_goal': 'dano.execution.page.flow_materialization.builder', 'prepare_flow_spec_for_publish': 'dano.execution.page.flow_release', 'refresh_review_items': 'dano.execution.page.flow_materialization.review_items', 'sync_flow_spec_models': 'dano.execution.page.flow_materialization.builder', 'validate_flow_spec': 'dano.execution.page.flow_spec_validate', 'ALLOWED_CAPABILITY_KINDS': 'dano.execution.page.capability_kinds', 'READ_CAPABILITY_KINDS': 'dano.execution.page.capability_kinds', 'WRITE_CAPABILITY_KINDS': 'dano.execution.page.capability_kinds', '_ACTION_LABELS': 'dano.execution.page.capability_kinds', '_CAPABILITY_PATH_PREFIXES': 'dano.execution.page.capability_kinds', '_MUTATING_RECORD_KINDS': 'dano.execution.page.capability_kinds', '_business_type_for_param': 'dano.execution.page.capability_io', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_capability_operation_kind': 'dano.execution.page.capability_kinds', '_capability_schema_field_type': 'dano.execution.page.capability_io', '_capability_scoped_step_ids': 'dano.execution.page.capability_refs', '_is_write_step': 'dano.execution.page.capability_kinds', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_normalize_capability_relation_semantics': 'dano.execution.page.capability_nodes', '_option_source_step_ids': 'dano.execution.page.capability_refs', '_read_status_steps': 'dano.execution.page.capability_refs', '_remove_capability_step_nodes': 'dano.execution.page.capability_nodes', '_repeated_write_command_signature': 'dano.execution.page.capability_kinds', '_response_identity_match_count': 'dano.execution.page.capability_refs', '_schema_node_at_path': 'dano.execution.page.capability_io', '_step_page_id_from_facts': 'dano.execution.page.capability_refs', '_sync_capability_order': 'dano.execution.page.capability_orchestration', '_write_contract_is_batch': 'dano.execution.page.capability_kinds'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
