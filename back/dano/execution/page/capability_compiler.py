"""Compile public capabilities from anchors and grounded request facts."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
    READ_CAPABILITY_KINDS,
    WRITE_CAPABILITY_KINDS,
    _apply_semantic_business_understanding,
    _capability_operation_kind,
    _default_capability_nodes,
    executable_flow_links,
    _ordered_capability_request_refs,
    _step_has_stable_record_identity,
    _write_contract_is_batch,
    _semantic_plan_coverage,
    _stable_json_hash,
    _sync_capability_io_schemas,
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


def _normalized_request_path(url_or_path: str) -> str:
    raw = str(url_or_path or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw else raw.split("?", 1)[0]
    path = (path or raw.split("?", 1)[0]).strip()
    if path and not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or path


def _step_host(step: FlowStep) -> str:
    raw = str(step.url or "")
    if "://" in raw:
        return urlparse(raw).netloc.casefold()
    return str((step.source_meta or {}).get("host") or "").casefold()


def _step_transaction_id(step: FlowStep) -> str:
    meta = step.source_meta or {}
    return str(
        meta.get("trigger_transaction_id")
        or meta.get("transaction_id")
        or ""
    )


def _query_signature(url_or_query: str | dict | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if isinstance(url_or_query, dict):
        items = {
            str(key): tuple(str(item) for item in (value if isinstance(value, list) else [value]))
            for key, value in url_or_query.items()
        }
        return tuple(sorted((key, items[key]) for key in items))
    raw = str(url_or_query or "")
    query = raw.split("?", 1)[1] if "?" in raw else (urlparse(raw).query if "://" in raw else "")
    parsed = parse_qs(query, keep_blank_values=True) if query else {}
    return tuple(sorted((key, tuple(values)) for key, values in parsed.items()))


def _body_signature(value: Any) -> str:
    if value in (None, "", {}, []):
        return ""
    return _stable_json_hash(value)


def _match_option_source_step(spec: FlowSpec, source: dict[str, Any]) -> FlowStep | None:
    """Resolve one option-source step; return None instead of guessing."""
    request_id = str(source.get("request_id") or source.get("source_request_id") or "")
    if request_id:
        step = next(
            (item for item in spec.steps if _request_id_for_step(spec, item) == request_id),
            None,
        )
        if step is not None:
            return step
    step_id = str(source.get("step_id") or source.get("source_step_id") or "")
    if step_id:
        step = next((item for item in spec.steps if item.step_id == step_id), None)
        if step is not None:
            return step
    source_url = str(source.get("source_url") or source.get("url") or source.get("path") or "")
    if not source_url:
        return None
    source_path = _normalized_request_path(source_url)
    if not source_path:
        return None
    parsed = urlparse(source_url) if "://" in source_url else None
    source_host = (parsed.netloc.casefold() if parsed is not None else str(source.get("host") or "").casefold())
    source_method = str(source.get("source_method") or source.get("method") or "").upper()
    source_page = str(source.get("page_id") or "")
    source_frame = str(source.get("frame_id") or "")
    source_tx = str(
        source.get("transaction_id")
        or source.get("trigger_transaction_id")
        or source.get("source_transaction_id")
        or ""
    )
    source_query = _query_signature(source_url if "?" in source_url else source.get("query"))
    source_body = _body_signature(source.get("source_body") or source.get("body"))
    candidates = [
        step for step in spec.steps
        if _normalized_request_path(step.path or step.url) == source_path
    ]
    if source_host:
        candidates = [step for step in candidates if _step_host(step) == source_host]
    if source_method:
        candidates = [
            step for step in candidates
            if (step.method or "GET").upper() == source_method
        ]
    if source_page:
        narrowed = [
            step for step in candidates
            if str((step.source_meta or {}).get("page_id") or "") == source_page
        ]
        if narrowed:
            candidates = narrowed
    if source_frame:
        narrowed = [
            step for step in candidates
            if str((step.source_meta or {}).get("frame_id") or "") == source_frame
        ]
        if narrowed:
            candidates = narrowed
    if source_tx:
        narrowed = [step for step in candidates if _step_transaction_id(step) == source_tx]
        if narrowed:
            candidates = narrowed
    if len(candidates) > 1 and source_query:
        narrowed = [step for step in candidates if _query_signature(step.url) == source_query]
        if len(narrowed) == 1:
            candidates = narrowed
        elif narrowed:
            candidates = narrowed
        else:
            return None
    if len(candidates) > 1 and source_body:
        narrowed = [
            step for step in candidates
            if _body_signature(getattr(step, "body_source", None) or (step.source_meta or {}).get("post_data"))
            == source_body
        ]
        if len(narrowed) == 1:
            candidates = narrowed
        elif not narrowed:
            return None
        else:
            candidates = narrowed
    if len(candidates) != 1:
        return None
    return candidates[0]


def _option_source_request_ids(
    spec: FlowSpec,
    member_steps: list[FlowStep],
    semantic_plan: dict[str, Any],
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
        request_id = str(source.get("request_id") or source.get("source_request_id") or "")
        step_id = str(source.get("step_id") or source.get("source_step_id") or "")
        capability_name = str(source.get("capability") or "")
        if not request_id and step_id:
            step = next((item for item in spec.steps if item.step_id == step_id), None)
            request_id = _request_id_for_step(spec, step) if step is not None else ""
        if not request_id and capability_name in plan_by_name:
            anchor = str(plan_by_name[capability_name].get("anchor_step_id") or "")
            step = next((item for item in spec.steps if item.step_id == anchor), None)
            request_id = _request_id_for_step(spec, step) if step is not None else ""
        if not request_id:
            step = _match_option_source_step(spec, source)
            if step is not None:
                request_id = _request_id_for_step(spec, step) or f"__step__:{step.step_id}"
        if request_id and request_id not in ids:
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
        anchor_step_id = str(item.get("anchor_step_id") or "").strip()
        anchor = by_step.get(anchor_step_id) or by_request.get(anchor_step_id)
        if anchor is not None:
            anchor_step_id = anchor.step_id
        prefix = f"semantic_plan.capabilities[{index}]"
        if not name or name in seen_names:
            errors.append(f"{prefix}: capability name is missing or duplicated")
            continue
        if not anchor_step_id or anchor is None:
            errors.append(f"{prefix}: anchor_step_id does not identify one materialized step")
            continue
        if anchor_step_id in seen_anchors:
            errors.append(f"{prefix}: public anchor is already owned by another capability")
            continue
        grounded_kind = _capability_operation_kind(anchor)
        is_write = grounded_kind in WRITE_CAPABILITY_KINDS
        if (is_write and kind not in WRITE_CAPABILITY_KINDS) or (
            not is_write and kind not in READ_CAPABILITY_KINDS
        ):
            errors.append(f"{prefix}: capability kind does not match the grounded business operation")
            continue
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
            continue
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
        for request_id in _option_source_request_ids(current, member_steps, plan):
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
