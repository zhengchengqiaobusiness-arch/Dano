"""Compile public capabilities from anchors and grounded request facts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from dano.execution.page.flow_spec_core.models import (
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _stable_json_hash,
)
from dano.execution.page.flow_spec_core.owner_runtime import (
    bind_owner_runtime,
)
from dano.execution.page.capability_kinds import (
    READ_CAPABILITY_KINDS,
    WRITE_CAPABILITY_KINDS,
    _capability_operation_kind,
    _write_contract_is_batch,
)
from dano.execution.page.capability_semantic import (
    _apply_semantic_business_understanding,
    _complete_semantic_plan_from_spec,
    _required_public_action_request_ids,
    _semantic_plan_execute_request_ids,
    _semantic_plan_coverage,
    _step_is_write_preflight,
)
from dano.execution.page.capability_nodes import (
    _default_capability_nodes,
)
from dano.execution.page.capability_views import (
    executable_flow_links,
)
from dano.execution.page.capability_refs import (
    _ordered_capability_request_refs,
)
from dano.execution.page.capability_io import (
    _sync_capability_io_schemas,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _step_has_stable_record_identity,
)
from dano.execution.page.request_identity import (
    normalized_request_path,
    request_query_signature,
    unique_request_identity_match,
)


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SIMPLE_CALL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class CapabilityCompilation:
    spec: FlowSpec
    capabilities: list[FlowCapability]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)


def _trusted_verification_ids(spec: FlowSpec) -> set[str]:
    return {
        str(item.get("verification_id"))
        for item in (spec.meta or {}).get("verification_log") or []
        if isinstance(item, dict)
        and item.get("status") == "passed"
        and item.get("verification_id")
    }


def _stable_capability_id(name: str, kind: str, anchor_step_id: str) -> str:
    raw = f"{name}\0{kind}\0{anchor_step_id}".encode("utf-8")
    return f"cap_{hashlib.sha256(raw).hexdigest()[:16]}"


def _request_id_for_step(spec: FlowSpec, step: FlowStep) -> str:
    request_id = str((step.source_meta or {}).get("request_id") or "")
    if request_id:
        return request_id
    usage_match = next((
        request_id for request_id, usage in (spec.request_facts.usage or {}).items()
        if str(usage.materialized_step_id or "") == step.step_id
    ), "")
    return str(usage_match or "")


def _read_operation_signature(spec: FlowSpec, step: FlowStep) -> tuple:
    """Identify the same recorded read independently of its occurrence."""
    request_id = _request_id_for_step(spec, step)
    fact = next((
        item for item in spec.request_facts.requests or []
        if str(item.request_id or "") == request_id
    ), None)
    url_or_path = str(
        (fact.url or fact.path if fact is not None else "")
        or step.url
        or step.path
        or ""
    )
    query = (
        fact.query
        if fact is not None and isinstance(fact.query, dict) and fact.query
        else url_or_path
    )

    def query_values(key: str, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
        return (
            ("<record-identity>",)
            if normalized_key.endswith(("id", "ids"))
            else values
        )

    query_contract = tuple(
        (key, query_values(key, values))
        for key, values in request_query_signature(query)
    )
    return (
        str((fact.method if fact is not None else step.method) or "GET").upper(),
        normalized_request_path(url_or_path),
        query_contract,
    )


def _retarget_read_capabilities_from_write_preflight(
    spec: FlowSpec,
    plan_items: list[dict[str, Any]],
    candidates: list[tuple[str, FlowStep]],
) -> list[dict[str, str]]:
    """Move a read ability from edit hydration to its standalone occurrence."""
    by_step = {str(step.step_id or ""): step for step in spec.steps}
    by_request = _step_by_request_id(spec)

    def plan_anchor(item: dict[str, Any]) -> FlowStep | None:
        anchor_id = str(item.get("anchor_step_id") or "")
        anchor = by_step.get(anchor_id) or by_request.get(anchor_id)
        if anchor is not None:
            return anchor
        for ref in item.get("request_refs") or []:
            if not isinstance(ref, dict) or str(ref.get("usage") or "") != "execute":
                continue
            identifier = str(ref.get("step_id") or ref.get("request_id") or "")
            return by_step.get(identifier) or by_request.get(identifier)
        return None

    retargeted: list[dict[str, str]] = []
    for request_id, candidate in candidates:
        if (
            str(candidate.method or "GET").upper() != "GET"
            or _step_is_write_preflight(candidate)
        ):
            continue
        signature = _read_operation_signature(spec, candidate)
        matches: list[tuple[dict[str, Any], FlowStep]] = []
        for item in plan_items:
            anchor = plan_anchor(item)
            if (
                anchor is not None
                and _step_is_write_preflight(anchor)
                and _read_operation_signature(spec, anchor) == signature
            ):
                matches.append((item, anchor))
        if len(matches) != 1:
            continue
        item, previous = matches[0]
        item["anchor_step_id"] = candidate.step_id
        execute_ref_found = False
        for ref in item.get("request_refs") or []:
            if not isinstance(ref, dict) or str(ref.get("usage") or "") != "execute":
                continue
            ref["step_id"] = candidate.step_id
            if "request_id" in ref:
                ref["request_id"] = request_id
            execute_ref_found = True
        if not execute_ref_found:
            item.setdefault("request_refs", []).append({
                "request_id": request_id,
                "step_id": candidate.step_id,
                "usage": "execute",
            })
        previous_request_id = _request_id_for_step(spec, previous)
        write_owners: list[dict[str, Any]] = []
        for owner in plan_items:
            if owner is item:
                continue
            owner_anchor = plan_anchor(owner)
            if owner_anchor is None or str(owner_anchor.method or "GET").upper() in {"GET", "HEAD"}:
                continue
            explicitly_uses_preflight = any(
                isinstance(ref, dict)
                and str(ref.get("usage") or "") == "preflight"
                and (
                    str(ref.get("step_id") or "") == previous.step_id
                    or str(ref.get("request_id") or "") == previous_request_id
                )
                for ref in owner.get("request_refs") or []
            )
            dependency_ids, _errors = _grounded_dependency_order(
                spec, owner_anchor.step_id,
            )
            if explicitly_uses_preflight or previous.step_id in dependency_ids[:-1]:
                write_owners.append(owner)
        if len(write_owners) == 1:
            owner_refs = write_owners[0].setdefault("request_refs", [])
            existing_option_ids = {
                (
                    str(ref.get("request_id") or ""),
                    str(ref.get("step_id") or ""),
                )
                for ref in owner_refs
                if isinstance(ref, dict) and str(ref.get("usage") or "") == "option_source"
            }
            transferred = [
                ref for ref in item.get("request_refs") or []
                if isinstance(ref, dict) and str(ref.get("usage") or "") == "option_source"
            ]
            item["request_refs"] = [
                ref for ref in item.get("request_refs") or []
                if not (isinstance(ref, dict) and str(ref.get("usage") or "") == "option_source")
            ]
            for ref in transferred:
                identity = (
                    str(ref.get("request_id") or ""),
                    str(ref.get("step_id") or ""),
                )
                if identity not in existing_option_ids:
                    owner_refs.append(deepcopy(ref))
                    existing_option_ids.add(identity)
        retargeted.append({
            "capability": str(item.get("name") or ""),
            "from_request_id": previous_request_id,
            "to_request_id": request_id,
        })
    return retargeted


def _request_ref(
    spec: FlowSpec,
    step: FlowStep | None,
    *,
    usage: str,
    request_id: str = "",
) -> CapabilityRequestRef:
    request_id = request_id or (_request_id_for_step(spec, step) if step is not None else "")
    fact = next((
        item for item in (spec.request_facts.requests or [])
        if str(item.request_id or "") == request_id
    ), None)
    return CapabilityRequestRef(
        request_id=request_id,
        request_index=(fact.request_index if fact is not None else (step.source_meta or {}).get("request_index") if step else None),
        step_id=step.step_id if step is not None else "",
        role=str(
            ((spec.request_facts.analysis or {}).get(request_id).role if request_id in (spec.request_facts.analysis or {}) else "")
            or ((step.source_meta or {}).get("role") if step else "")
            or (step.semantic_role if step else "")
            or ""
        ),
        method=str((fact.method if fact is not None else step.method if step else "") or "").upper(),
        path=str((fact.path or fact.url if fact is not None else step.path or step.url if step else "") or ""),
        sequence=(fact.sequence if fact is not None else (step.source_meta or {}).get("sequence") if step else None),
        confidence=1.0,
        reason="成员由机器验证或录制值匹配的请求图确定性编译",
        usage=usage,
        origin="compiler",
        confirmed=True,
    )


def _executable_links(spec: FlowSpec):  # noqa: ANN202
    yield from executable_flow_links(spec)


def _grounded_dependency_order(
    spec: FlowSpec,
    anchor_step_id: str,
    *,
    include_collection_sources: bool = True,
) -> tuple[list[str], list[str]]:
    position = {step.step_id: index for index, step in enumerate(spec.steps)}
    upstream: dict[str, list[str]] = {}
    for link in _executable_links(spec):
        if not include_collection_sources and "[" in str(link.source_path or ""):
            # A read capability must accept the selected record identity; it
            # must not silently execute a list query and choose one row.
            continue
        upstream.setdefault(link.target_step_id, []).append(link.source_step_id)
    for target in upstream:
        upstream[target] = sorted(set(upstream[target]), key=lambda step_id: position.get(step_id, 10**9))

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[str] = []

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            errors.append(f"grounded dependency cycle reaches anchor {anchor_step_id}: {step_id}")
            return
        visiting.add(step_id)
        for source_step_id in upstream.get(step_id, []):
            visit(source_step_id)
        visiting.remove(step_id)
        visited.add(step_id)
        ordered.append(step_id)

    visit(anchor_step_id)
    return ordered, errors


def _step_by_request_id(spec: FlowSpec) -> dict[str, FlowStep]:
    out: dict[str, FlowStep] = {}
    for step in spec.steps:
        request_id = _request_id_for_step(spec, step)
        if request_id:
            out[request_id] = step
    return out


def _planned_anchor(
    item: dict[str, Any],
    *,
    by_step: dict[str, FlowStep],
    by_request: dict[str, FlowStep],
    seen_anchors: set[str],
) -> FlowStep | None:
    """Resolve the real execute anchor even when a stale live ID survived freeze."""
    identifiers = [item.get("anchor_step_id")]
    identifiers.extend(
        value
        for ref in item.get("request_refs") or []
        if isinstance(ref, dict) and str(ref.get("usage") or "") == "execute"
        for value in (ref.get("step_id"), ref.get("request_id"))
    )
    candidates: list[FlowStep] = []
    for identifier in identifiers:
        raw = str(identifier or "")
        step = by_step.get(raw) or by_request.get(raw)
        if step is not None and step not in candidates and step.step_id not in seen_anchors:
            candidates.append(step)
    requested_kind = str(item.get("kind") or "")
    requested_write = requested_kind in WRITE_CAPABILITY_KINDS
    same_family = [
        step for step in candidates
        if (_capability_operation_kind(step) in WRITE_CAPABILITY_KINDS) == requested_write
    ]
    if same_family:
        return same_family[0]
    return candidates[0] if candidates else None


def _match_option_source_step(spec: FlowSpec, source: dict[str, Any]) -> FlowStep | None:
    """Resolve one option-source step; return None instead of guessing."""
    if not any(source.get(key) for key in (
        "request_id", "source_request_id", "step_id", "source_step_id",
        "source_url", "url", "path",
    )):
        return None
    return unique_request_identity_match(source, (
        (step, {
            **dict(step.source_meta or {}),
            "request_id": _request_id_for_step(spec, step),
            "step_id": step.step_id,
            "method": step.method,
            "url": step.url or step.path,
            "path": step.path,
            "body": step.body_source,
            "content_type": step.content_type,
        })
        for step in spec.steps
    ))


def _option_source_request_ids(
    spec: FlowSpec,
    member_steps: list[FlowStep],
    semantic_plan: dict[str, Any],
    capability_plan: dict[str, Any] | None = None,
) -> list[str]:
    ids: list[str] = []
    plan_by_name = {
        str(item.get("name") or ""): item
        for item in semantic_plan.get("capabilities") or []
        if isinstance(item, dict) and item.get("name")
    }

    def add_source(source: object) -> None:
        if not isinstance(source, dict):
            return
        request_id = ""
        capability_name = str(source.get("capability") or "")
        if capability_name in plan_by_name:
            anchor = str(plan_by_name[capability_name].get("anchor_step_id") or "")
            step = next((item for item in spec.steps if item.step_id == anchor), None)
            request_id = _request_id_for_step(spec, step) if step is not None else ""
        if not request_id:
            step = _match_option_source_step(spec, source)
            if step is not None:
                request_id = _request_id_for_step(spec, step) or f"__step__:{step.step_id}"
        if request_id and request_id not in ids:
            ids.append(request_id)

    # The planner cannot add executable members, but an explicitly named
    # option-source request is part of the public field contract.  Retain it
    # only by exact materialized step/request identity; never replace it with
    # another capture merely because the endpoint path is the same.
    by_step_id = {step.step_id: step for step in spec.steps}
    by_request_id = _step_by_request_id(spec)
    for request_ref in (capability_plan or {}).get("request_refs") or []:
        if (
            not isinstance(request_ref, dict)
            or str(request_ref.get("usage") or "") != "option_source"
        ):
            continue
        identifier = str(
            request_ref.get("step_id")
            or request_ref.get("request_id")
            or ""
        )
        option_step = by_step_id.get(identifier) or by_request_id.get(identifier)
        if option_step is None:
            continue
        request_id = _request_id_for_step(spec, option_step) or f"__step__:{option_step.step_id}"
        if request_id not in ids:
            ids.append(request_id)

    for step in member_steps:
        for binding in step.selects or []:
            add_source({
                "request_id": binding.source_request_id,
                "source_url": binding.source_url,
                "source_method": binding.source_method,
                "source_body": binding.source_body,
            })
        for param in step.params or []:
            source = dict(param.source or {})
            if param.source_kind == "api_option":
                add_source(source)
            add_source(source.get("option_source") if isinstance(source.get("option_source"), dict) else None)
    member_ids = {step.step_id for step in member_steps}
    for link in spec.links or []:
        if link.confirmed and link.target_step_id in member_ids:
            add_source((link.value_binding or {}).get("option_source"))
    for link in _executable_links(spec):
        if link.target_step_id in member_ids:
            add_source((link.value_binding or {}).get("option_source"))
    return ids


def _verified_fact_check_request_id(spec: FlowSpec, anchor: FlowStep) -> str:
    fact_check = dict(anchor.fact_check or {})
    verification_id = str(fact_check.get("verification_id") or "")
    if (
        fact_check.get("verified") is True
        and verification_id in _trusted_verification_ids(spec)
    ):
        return str(fact_check.get("source_request_id") or "")
    return ""


def _step_matching_request(
    spec: FlowSpec,
    request_id: str,
    by_request: dict[str, FlowStep],
    by_step: dict[str, FlowStep],
) -> FlowStep | None:
    if request_id.startswith("__step__:"):
        return by_step.get(request_id.removeprefix("__step__:"))
    step = by_request.get(request_id)
    if step is not None:
        return step
    fact = next(
        (
            item for item in spec.request_facts.requests
            if str(item.request_id or "") == request_id
        ),
        None,
    )
    if fact is None:
        return None
    return _match_option_source_step(spec, {
        "source_url": fact.url or fact.path,
        "method": fact.method,
        "page_id": fact.page_id or "",
        "frame_id": fact.frame_id or "",
        "query": fact.query,
        "transaction_id": str(getattr(fact, "trigger_transaction_id", "") or ""),
    })


def _compiled_nodes_from_refs(
    refs: list[CapabilityRequestRef],
    anchor_step_id: str,
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, ref in enumerate(refs, 1):
        if ref.usage == "option_source" and not ref.step_id:
            continue
        node = {
            "id": f"call_{index}",
            "type": "call",
            "usage": ref.usage,
            "request_id": ref.request_id,
            "method": ref.method,
            "path": ref.path,
        }
        if ref.step_id:
            node["step_id"] = ref.step_id
        nodes.append(node)
    nodes.append({
        "id": "return_final",
        "type": "return",
        "from": anchor_step_id,
        "path": "response",
    })
    return nodes


def _has_stable_record_identity(step: FlowStep) -> bool:
    return _step_has_stable_record_identity(step)


def _goal_update_is_grounded_by_sibling_create(
    plan_items: list[dict[str, Any]],
    by_step: dict[str, FlowStep],
    item: dict[str, Any],
    anchor: FlowStep,
    grounded_kind: str,
) -> bool:
    """Keep update distinct when one endpoint serves old and new records.

    The goal label alone is not evidence.  The distinction is accepted only
    when this anchor carries a stable record identity and the same captured
    write endpoint also has a planned create/submit anchor without one.
    """
    if (
        str(item.get("kind") or "") != "update"
        or str(item.get("kind_source") or "") != "recording_goal"
        or grounded_kind not in {"create", "submit"}
        or not _has_stable_record_identity(anchor)
    ):
        return False
    method = str(anchor.method or "POST").upper()
    path = urlparse(str(anchor.path or anchor.url or "")).path
    for sibling in plan_items:
        if sibling is item or str(sibling.get("kind") or "") not in {"create", "submit"}:
            continue
        sibling_anchor = by_step.get(str(sibling.get("anchor_step_id") or ""))
        if sibling_anchor is None:
            continue
        sibling_path = urlparse(str(sibling_anchor.path or sibling_anchor.url or "")).path
        if (
            str(sibling_anchor.method or "POST").upper() == method
            and sibling_path == path
            and not _has_stable_record_identity(sibling_anchor)
        ):
            return True
    return False


def _normalize_compiled_call_keys(
    spec: FlowSpec,
    capabilities: list[FlowCapability],
) -> None:
    """Keep display semantics while compiling stable caller keys.

    Recording-owned ParamFields intentionally use localized business names as
    ``key``.  The generated ability is a separate copy: for a simple request
    leaf that was named automatically from the page, its invocation key must
    stay equal to the wire leaf.  Explicit agent/operator renames and derived
    structures remain untouched.
    """
    compiled_step_ids = {
        step_id
        for capability in capabilities
        for step_id in capability.step_ids
    }
    for step in spec.steps:
        if step.step_id not in compiled_step_ids:
            continue
        candidates: list[tuple[Any, str]] = []
        for param in step.params:
            if not (param.exposed_to_user and param.category == "user_param"):
                continue
            if param.locked or param.name_source in {"agent", "manual"}:
                continue
            source_contract_kind = str((param.source or {}).get("kind") or "")
            if (
                param.source_kind in {"dynamic_structure", "dynamic_structure_input"}
                or source_contract_kind in {"dynamic_structure", "dynamic_structure_input"}
            ):
                continue
            relative = str(param.path or "")
            for prefix in ("body.", "query.", "path."):
                if relative.startswith(prefix):
                    relative = relative[len(prefix):]
                    break
            if not _SIMPLE_CALL_KEY.fullmatch(relative):
                continue
            candidates.append((param, relative))
        existing = {str(param.key or "") for param in step.params}
        for param, call_key in candidates:
            if call_key != param.key and call_key in existing:
                continue
            old_key = str(param.key or "")
            # Older captures stored the page-facing business name in ``key``
            # and left ``label`` empty.  Normalizing the callable key must not
            # erase that independent name axis.
            if old_key and old_key != call_key and not str(param.label or "").strip():
                param.label = old_key
            existing.discard(str(param.key or ""))
            existing.add(call_key)
            param.key = call_key
            param.evidence = [*list(param.evidence or []), {
                "source": "capability_compiler",
                "field": "key",
                "previous": old_key,
                "value": call_key,
                "reason": "能力调用键与请求 wire 叶子保持稳定，显示名继续使用页面业务名称",
            }]
            if old_key in step.sample_inputs:
                step.sample_inputs[call_key] = step.sample_inputs.pop(old_key)
            elif param.value not in (None, ""):
                step.sample_inputs.setdefault(call_key, param.value)
            for binding in step.selects:
                if binding.path == param.path or binding.param == old_key:
                    binding.param = call_key


def compile_capabilities(spec: FlowSpec, semantic_plan: dict[str, Any]) -> CapabilityCompilation:
    """Return a copy whose generated abilities use only verified graph membership.

    The Skill plan names the capability, public copy, and public anchor. Kind
    and title stay Skill-owned when they match the recorded read/write family.
    Model-supplied request membership is ignored; the grounded graph supplies
    members.
    """
    bind_owner_runtime()
    current = spec.model_copy(deep=True)
    plan = semantic_plan if isinstance(semantic_plan, dict) else {}
    plan_items = [item for item in plan.get("capabilities") or [] if isinstance(item, dict)]
    by_step = {step.step_id: step for step in current.steps}
    by_request = _step_by_request_id(current)
    errors: list[str] = []
    warnings: list[str] = []
    compiled: list[FlowCapability] = []
    seen_names: set[str] = set()
    seen_anchors: set[str] = set()

    for index, item in enumerate(plan_items):
        name = str(item.get("name") or "").strip()
        title = str(item.get("title") or name).strip()
        kind = str(item.get("kind") or "").strip()
        anchor = _planned_anchor(
            item,
            by_step=by_step,
            by_request=by_request,
            seen_anchors=seen_anchors,
        )
        anchor_step_id = anchor.step_id if anchor is not None else ""
        prefix = f"semantic_plan.capabilities[{index}]"
        if not name or name in seen_names:
            errors.append(f"{prefix}: capability name is missing or duplicated")
            continue
        if not anchor_step_id or anchor is None:
            errors.append(
                f"{prefix}: anchor_step_id and execute references do not identify "
                "one unused materialized step"
            )
            continue
        grounded_kind = _capability_operation_kind(anchor)
        is_write = grounded_kind in WRITE_CAPABILITY_KINDS
        if (is_write and kind not in WRITE_CAPABILITY_KINDS) or (
            not is_write and kind not in READ_CAPABILITY_KINDS
        ):
            warnings.append(
                f"{prefix}: capability kind {kind!r} was normalized to grounded kind {grounded_kind!r}"
            )
            kind = grounded_kind
        grounded_batch = bool(is_write and _write_contract_is_batch(current, [anchor]))
        grounded_goal_update = _goal_update_is_grounded_by_sibling_create(
            plan_items, by_step, item, anchor, grounded_kind,
        )
        if grounded_batch:
            kind = "submit_batch"
        elif grounded_goal_update:
            kind = "update"
        elif kind != grounded_kind:
            warnings.append(
                f"{prefix}: skill kind {kind!r} differs from structural hint {grounded_kind!r}"
            )

        step_ids, dependency_errors = _grounded_dependency_order(
            current,
            anchor_step_id,
            include_collection_sources=is_write,
        )
        errors.extend(f"{prefix}: {message}" for message in dependency_errors)
        if dependency_errors:
            # A broken optional relation must not erase the public action. Keep
            # the executable anchor and leave the relation error for repair.
            step_ids = [anchor_step_id]
        member_steps = [by_step[step_id] for step_id in step_ids]
        refs = [
            _request_ref(
                current,
                step,
                usage="execute" if step.step_id == anchor_step_id else "preflight",
            )
            for step in member_steps
        ]
        occupied_ids = {ref.request_id for ref in refs if ref.request_id}
        for request_id in _option_source_request_ids(
            current, member_steps, plan, capability_plan=item,
        ):
            if request_id in occupied_ids:
                continue
            option_step = _step_matching_request(current, request_id, by_request, by_step)
            refs.append(_request_ref(
                current,
                option_step,
                usage="option_source",
                request_id="" if request_id.startswith("__step__:") else request_id,
            ))
            occupied_ids.add(request_id)
        fact_check_request_id = _verified_fact_check_request_id(current, anchor)
        if fact_check_request_id and fact_check_request_id not in occupied_ids:
            refs.append(_request_ref(
                current,
                by_request.get(fact_check_request_id),
                usage="fact_check",
                request_id=fact_check_request_id,
            ))
        refs = _ordered_capability_request_refs(refs)

        compiled.append(FlowCapability(
            name=name,
            title=title,
            intent=str(item.get("intent") or title or name),
            kind=kind,
            capability_id=_stable_capability_id(name, kind, anchor_step_id),
            request_refs=refs,
            step_ids=step_ids,
            nodes=(
                _default_capability_nodes(member_steps, kind="submit_batch")
                if kind == "submit_batch"
                else _compiled_nodes_from_refs(refs, anchor_step_id)
            ),
            confirmed=False,
            confidence=1.0,
            evidence=[{
                "source": "grounded_request_graph",
                "anchor_step_id": anchor_step_id,
                "ignored_model_request_refs": len(item.get("request_refs") or []),
            }],
            status="draft",
            updated_by="planner",
        ))
        seen_names.add(name)
        seen_anchors.add(anchor_step_id)

    preserved = [
        capability.model_copy(deep=True)
        for capability in current.capabilities or []
        if capability.locked
        or capability.updated_by == "user"
        or any(ref.origin in {"manual", "user"} for ref in capability.request_refs or [])
    ]
    current.capabilities = [*preserved, *compiled]
    _normalize_compiled_call_keys(current, compiled)
    valid_refs = {
        ref for capability in current.capabilities
        for ref in (capability.name, capability.capability_id) if ref
    }
    current.capability_relations = [
        relation for relation in current.capability_relations or []
        if relation.from_capability in valid_refs and relation.to_capability in valid_refs
    ]
    current = _sync_capability_io_schemas(current)
    current = _apply_semantic_business_understanding(current, plan)
    audit = {
        "protocol": "dano.capability_compilation.v1",
        "planned": len(plan_items),
        "compiled": len(compiled),
        "preserved_human": len(preserved),
        "errors": list(errors),
        "warnings": list(warnings),
    }
    current.meta = {**(current.meta or {}), "capability_compilation": audit}
    generation = dict((current.meta or {}).get("capability_generation") or {})
    if generation:
        coverage = _semantic_plan_coverage(
            current,
            {"semantic_plan": plan},
        )
        if coverage.get("complete") and not errors:
            current.meta = {
                **(current.meta or {}),
                "capability_generation": {
                    **generation,
                    "initial_completed": True,
                    "semantic_plan_hash": _stable_json_hash(plan),
                    "status": "ready",
                },
            }
            model = dict((current.meta or {}).get("capability_model") or {})
            if model:
                current.meta["capability_model"] = {
                    **model,
                    "status": "ready",
                    "source": "verified_request_graph",
                    "semantic_plan": plan,
                    "semantic_coverage": coverage,
                    "capability_compilation": audit,
                }
    return CapabilityCompilation(
        spec=current,
        capabilities=list(current.capabilities),
        errors=errors,
        warnings=warnings,
        audit=audit,
    )


def ensure_grounded_capability_output(spec: FlowSpec) -> FlowSpec:
    """Keep every recorded independent business action as a Stage 6 ability.

    The model-authored plan remains the primary source of public semantics.
    This fallback only fills a missing structural boundary from an immutable
    request/action and lets the normal compiler derive membership and order.
    """
    from dano.execution.page.capability_contracts import (
        _mark_repeated_write_observations,
        _planned_capability_has_public_anchor,
    )
    from dano.execution.page.capability_kinds import _is_write_step
    from dano.execution.page.flow_materialization.request_steps import promote_request_to_step
    from dano.execution.page.recording_live import _recording_goal_contract

    current = spec.model_copy(deep=True)
    _mark_repeated_write_observations(current)
    required = _required_public_action_request_ids(current)
    ordered_request_ids = [
        str(fact.request_id or "")
        for fact in current.request_facts.requests or []
        if str(fact.request_id or "") in required
    ]
    by_request = _step_by_request_id(current)
    promotion_errors: list[str] = []
    for request_id in ordered_request_ids:
        if request_id in by_request:
            continue
        try:
            promote_request_to_step(current, request_id=request_id)
        except (TypeError, ValueError) as exc:
            promotion_errors.append(f"{request_id}: {exc}")
    by_request = _step_by_request_id(current)

    candidates: list[tuple[str, FlowStep]] = [
        (request_id, by_request[request_id])
        for request_id in ordered_request_ids
        if request_id in by_request
        and not (by_request[request_id].source_meta or {}).get("duplicate_observation_of")
    ]
    if not candidates:
        candidates = [
            (_request_id_for_step(current, step) or step.step_id, step)
            for step in current.steps
            if not (step.source_meta or {}).get("duplicate_observation_of")
            and (
                _is_write_step(step)
                or _planned_capability_has_public_anchor(
                    current, _capability_operation_kind(step), [step.step_id],
                )
            )
        ]

    model = dict((current.meta or {}).get("capability_model") or {})
    previous_fallback_names = {
        str(name or "")
        for name in model.get("fallback_added_capabilities") or []
        if str(name or "")
    }
    if previous_fallback_names:
        current.capabilities = [
            capability for capability in current.capabilities
            if capability.name not in previous_fallback_names
            or capability.locked
            or capability.updated_by == "user"
            or any(
                ref.origin in {"manual", "user"}
                for ref in capability.request_refs or []
            )
        ]
    existing_plan = (
        model.get("submitted_semantic_plan")
        if isinstance(model.get("submitted_semantic_plan"), dict)
        else model.get("semantic_plan") if isinstance(model.get("semantic_plan"), dict) else {}
    )
    if previous_fallback_names and isinstance(existing_plan, dict):
        existing_plan = deepcopy(existing_plan)
        existing_plan["capabilities"] = [
            item for item in existing_plan.get("capabilities") or []
            if not isinstance(item, dict)
            or str(item.get("name") or "") not in previous_fallback_names
        ]
    plan = _complete_semantic_plan_from_spec(current, existing_plan)
    plan.setdefault("business_understanding", {
        "business_name": str(current.title or "").strip(),
        "summary": str(current.business_description or "").strip(),
    })
    plan.setdefault("unresolved_items", [])
    plan_items = [
        deepcopy(item) for item in plan.get("capabilities") or [] if isinstance(item, dict)
    ]
    plan["capabilities"] = plan_items
    retargeted_capabilities = _retarget_read_capabilities_from_write_preflight(
        current, plan_items, candidates,
    )
    covered = _semantic_plan_execute_request_ids(current, plan)
    used_names = {str(item.get("name") or "") for item in plan_items}
    goal_slots = list(_recording_goal_contract(current).get("capabilities") or [])
    fallback_names: list[str] = []

    for index, (request_id, step) in enumerate(candidates):
        if request_id in covered or step.step_id in covered:
            continue
        kind = _capability_operation_kind(step)
        goal_title = str(
            (goal_slots[index].get("name") if index < len(goal_slots) else "") or ""
        ).strip()
        title = goal_title or str(step.name or "").strip() or kind.replace("_", " ")
        raw_name = re.sub(
            r"[^a-z0-9]+", "_", (goal_title or str(step.name or "")).casefold(),
        ).strip("_")
        name = raw_name or f"{kind}_{hashlib.sha1(request_id.encode('utf-8')).hexdigest()[:8]}"
        if name in used_names:
            name = f"{name}_{hashlib.sha1(request_id.encode('utf-8')).hexdigest()[:6]}"
        used_names.add(name)
        fallback_names.append(name)
        plan_items.append({
            "name": name,
            "title": title,
            "intent": title,
            "kind": kind,
            "anchor_step_id": step.step_id,
            "request_refs": [{
                "request_id": request_id,
                "step_id": step.step_id,
                "usage": "execute",
            }],
        })
        covered.add(request_id)

    current_names = [str(capability.name or "") for capability in current.capabilities]
    planned_names = [str(item.get("name") or "") for item in plan_items]
    if not plan_items or (
        not fallback_names
        and not retargeted_capabilities
        and len(current_names) == len(planned_names)
        and set(current_names) == set(planned_names)
    ):
        return current

    compilation = compile_capabilities(current, plan)
    if compilation.capabilities:
        current = compilation.spec
    materialized_names = [str(capability.name or "") for capability in current.capabilities]
    submitted_names = [str(item.get("name") or "") for item in plan_items]
    missing_names = sorted(set(submitted_names) - set(materialized_names))
    current.meta = {
        **(current.meta or {}),
        "capability_model": {
            **model,
            "status": "ready" if not missing_names else "needs_review",
            "source": (
                "grounded_action_fallback"
                if fallback_names else "grounded_action_reconciliation"
            ),
            "semantic_plan": plan,
            "submitted_semantic_plan": plan,
            "submitted_count": len(plan_items),
            "submitted_names": submitted_names,
            "materialized_count": len(current.capabilities),
            "materialized_names": materialized_names,
            "missing_submitted_names": missing_names,
            "extra_materialized_names": sorted(set(materialized_names) - set(submitted_names)),
            "semantic_coverage": _semantic_plan_coverage(current, {"semantic_plan": plan}),
            "capability_compilation": compilation.audit,
            "capability_compilation_errors": [*promotion_errors, *compilation.errors],
            "fallback_added_capabilities": fallback_names,
            "retargeted_capabilities": retargeted_capabilities,
        },
    }
    return current
