"""Compile public capabilities from anchors and grounded request facts."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
    READ_CAPABILITY_KINDS,
    WRITE_CAPABILITY_KINDS,
    _capability_operation_kind,
    _default_capability_nodes,
    _write_contract_is_batch,
    _semantic_plan_coverage,
    _stable_json_hash,
    sync_capability_scoped_views,
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


def _stable_capability_id(name: str, kind: str, anchor_step_id: str) -> str:
    raw = f"{name}\0{kind}\0{anchor_step_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


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


def _trusted_verification_ids(spec: FlowSpec) -> set[str]:
    return {
        str(item.get("verification_id"))
        for item in (spec.meta or {}).get("verification_log") or []
        if isinstance(item, dict)
        and item.get("status") == "passed"
        and item.get("verification_id")
    }


def _executable_links(spec: FlowSpec):  # noqa: ANN202
    trusted = _trusted_verification_ids(spec)
    for link in spec.links or []:
        meta = dict(link.meta or {})
        verification_id = str(meta.get("verification_id") or (link.evidence or {}).get("verification_id") or "")
        active = meta.get("active", True) is not False and getattr(link, "active", True) is not False
        machine_verified = (
            link.confirmed
            and meta.get("verified") is True
            and verification_id in trusted
        )
        capture_grounded = (
            meta.get("captured_value_match") is True
            or meta.get("captured_structure_match") is True
        )
        if active and (machine_verified or capture_grounded):
            yield link


def _grounded_dependency_order(spec: FlowSpec, anchor_step_id: str) -> tuple[list[str], list[str]]:
    position = {step.step_id: index for index, step in enumerate(spec.steps)}
    upstream: dict[str, list[str]] = {}
    for link in _executable_links(spec):
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
        source_url = str(source.get("source_url") or source.get("url") or source.get("path") or "")
        if not request_id and step_id:
            step = next((item for item in spec.steps if item.step_id == step_id), None)
            request_id = _request_id_for_step(spec, step) if step is not None else ""
        if not request_id and capability_name in plan_by_name:
            anchor = str(plan_by_name[capability_name].get("anchor_step_id") or "")
            step = next((item for item in spec.steps if item.step_id == anchor), None)
            request_id = _request_id_for_step(spec, step) if step is not None else ""
        if not request_id and source_url:
            source_path = urlparse(source_url).path or source_url.split("?", 1)[0]
            step = next((
                item for item in spec.steps
                if (urlparse(str(item.path or item.url or "")).path or str(item.path or item.url or "").split("?", 1)[0])
                == source_path
            ), None)
            if step is not None:
                request_id = _request_id_for_step(spec, step) or f"__step__:{step.step_id}"
        if request_id and request_id not in ids:
            ids.append(request_id)

    for step in member_steps:
        for binding in step.selects or []:
            add_source({"request_id": binding.source_request_id})
        for param in step.params or []:
            add_source(param.source or {})
            add_source((param.source or {}).get("option_source"))
    member_ids = {step.step_id for step in member_steps}
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


def _compiled_nodes(step_ids: list[str], anchor_step_id: str) -> list[dict[str, Any]]:
    nodes = [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids, 1)
    ]
    nodes.append({"id": "return_final", "type": "return", "from": anchor_step_id, "path": "response"})
    return nodes


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

    The semantic plan may name a capability and select its public anchor. Any
    model-supplied request membership is deliberately ignored.
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
        anchor = by_step.get(anchor_step_id)
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
        if kind != grounded_kind and not (kind == "submit_batch" and grounded_batch):
            warnings.append(
                f"{prefix}: model kind {kind!r} replaced by grounded kind {grounded_kind!r}"
            )
        kind = "submit_batch" if grounded_batch else grounded_kind

        if is_write:
            step_ids, dependency_errors = _grounded_dependency_order(current, anchor_step_id)
            errors.extend(f"{prefix}: {message}" for message in dependency_errors)
        else:
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
        for request_id in _option_source_request_ids(current, member_steps, plan):
            if request_id in occupied_ids:
                continue
            step_only_id = request_id.removeprefix("__step__:") if request_id.startswith("__step__:") else ""
            option_step = by_step.get(step_only_id) if step_only_id else by_request.get(request_id)
            refs.append(_request_ref(
                current,
                option_step,
                usage="option_source",
                request_id="" if step_only_id else request_id,
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
                else _compiled_nodes(step_ids, anchor_step_id)
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
    current = sync_capability_scoped_views(current)
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
