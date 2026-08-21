"""Stage 5: captured value links and dependency structure."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING
from datetime import datetime, timezone
import hashlib
import json
import re
from dano.execution.page.flow_spec_core.models import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.request_capture import (
    _leaf_paths,
)
from dano.execution.page.recording_facts import (
    _list_payload_has_reference_contract,
    _looks_pagination_field,
    _request_path,
)
from dano.execution.page.flow_materialization.request_steps import (
    _step_sequence,
)

if TYPE_CHECKING:
    from dano.execution.page.flow_spec_core.normalization import (
        _FLOW_PATH_MISSING,
        _flow_path_lookup,
    )
    from dano.execution.page.flow_spec_core.controlled_edits import (
        _apply_link_sources,
        _reset_param_source,
        _resolve_param_reference,
    )
    from dano.execution.page.flow_materialization.field_contracts.common import (
        _looks_system_const_field,
        _looks_user_entered_business_field,
    )
    from dano.execution.page.flow_materialization.field_contracts.option_projection import (
        _OPTION_SOURCE_KINDS,
        _composite_values_match,
        _recorded_scalar_values_match,
    )
    from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
        _param_has_editable_control_evidence,
    )


def _link_is_auto_generated(lk: FlowLink) -> bool:
    reason = str(lk.reason or "")
    evidence = lk.evidence if isinstance(lk.evidence, dict) else {}
    if evidence.get("actor") == "agent" or (lk.meta or {}).get("actor") == "agent":
        return False
    return (
        not getattr(lk, "locked", False)
        and (
            "自动" in reason
            or "值" in reason
            or "匹配" in reason
            or evidence.get("kind") == "value_match"
            or evidence.get("kind") == "record_hydration"
            or evidence.get("auto_rebuilt") is True
        )
    )


def _auto_dependency_target_allowed(param: ParamField | None) -> bool:
    if param is None:
        return False
    if param.source_kind in _OPTION_SOURCE_KINDS:
        return False
    if param.type in {"enum", "list-enum"}:
        return False
    if param.enum_options:
        return False
    if _looks_pagination_field(param.key, param.path):
        return False
    if _looks_system_const_field(param.key, param.path):
        return False
    if param.category in {"system_const"} and param.source_kind != "page_default":
        return False
    if param.source_kind in {"constant", "page_context", "system_time", "system_generated", "computed", "current_user"}:
        return False
    return True


def _auto_dependency_link_allowed(param: ParamField | None, source_path: str, lk: FlowLink | None = None) -> bool:
    if lk is not None and not _link_is_auto_generated(lk):
        return True
    if param is None:
        return False
    evidence = lk.evidence if lk is not None and isinstance(lk.evidence, dict) else {}
    source_leaf = re.sub(
        r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower(),
    )
    target_leaf = re.sub(
        r"[^a-z0-9]+", "",
        str(param.path or param.key or "").split(".")[-1].lower(),
    )
    # Picking the first row of a previous *list* is not a dependency. The same
    # record's own line items in a detail response are hydration, not a list pick.
    if "[" in str(source_path or "") and not (
        evidence.get("kind") == "record_hydration"
        and source_leaf == target_leaf
        and int(evidence.get("match_count") or 0) >= 3
    ):
        return False
    if (
        lk is not None
        and lk.confirmed
        and float(lk.confidence or 0.0) >= 0.95
        and evidence.get("kind") == "record_hydration"
        and int(evidence.get("match_count") or 0) >= 3
        and bool(evidence.get("identity_paths"))
        and source_leaf == target_leaf
    ):
        return True
    if param.category == "user_param" or param.source_kind == "user_input" or _looks_user_entered_business_field(param.key, param.path):
        # A recorded value or a similar field name cannot prove that an editable
        # business field is supplied by an earlier response.  The exception is
        # an exact field projection observed in the same action chain: edit
        # forms use that value as an overrideable default, not as a hidden
        # runtime-only field.
        if (
            lk is not None
            and lk.confirmed
            and float(lk.confidence or 0.0) >= 0.95
            and evidence.get("same_action_chain") is True
            and _param_has_editable_control_evidence(param)
            and _dependency_match_score(param, source_path) >= 40
        ):
            return True
        evidence = lk.evidence if lk is not None and isinstance(lk.evidence, dict) else {}
        captured_match = evidence.get("captured_value_match")
        source_leaf = re.sub(
            r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower(),
        )
        target_leaf = re.sub(
            r"[^a-z0-9]+", "",
            str(param.path or param.key or "").split(".")[-1].lower(),
        )
        if (
            lk is not None
            and lk.confirmed
            and float(lk.confidence or 0.0) >= 0.95
            and isinstance(captured_match, dict)
            and int(captured_match.get("occurrences") or 0) == 1
            and not _param_has_editable_control_evidence(param)
            and source_leaf == "id"
            and target_leaf.endswith("id")
        ):
            return True
        # Manual links have already returned above; other automatic links need
        # a real runtime contract.
        return False
    if param is not None and lk is not None and lk.confirmed and float(lk.confidence or 0.0) >= 0.95:
        source_leaf = re.sub(r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower())
        target_leaf = re.sub(r"[^a-z0-9]+", "", str(param.path or param.key or "").split(".")[-1].lower())
        # 完整事实库已证明该真实值只来自一个响应端点时，允许通用 id -> *Id
        # 注入（典型为 data.id -> query.processDefinitionId）。这比字段名模糊匹配强，
        # 同时仍拒绝 title/date/status 等常见值造成的假关联。
        if source_leaf == "id" and target_leaf.endswith("id"):
            return True
        if _dependency_match_score(param, source_path) >= 40 and not _param_has_editable_control_evidence(param):
            # A read-only/default-free field with an exact wire-name match is a
            # grounded response projection, including short values such as 8.
            return True
    if not _auto_dependency_target_allowed(param):
        return False
    return True


def _auto_link_has_grounded_contract(steps: list[FlowStep], link: FlowLink) -> bool:
    by_id = {step.step_id: step for step in steps}
    positions = {step.step_id: index for index, step in enumerate(steps)}
    source = by_id.get(link.source_step_id)
    target = by_id.get(link.target_step_id)
    if source is None or target is None:
        return False
    source_sequence = _step_sequence(source)
    target_sequence = _step_sequence(target)
    if source_sequence is not None and target_sequence is not None:
        if source_sequence >= target_sequence:
            return False
    elif positions[source.step_id] >= positions[target.step_id]:
        return False
    if source.response_json is None:
        return False
    source_path = str(link.source_path or "").removeprefix("response.")
    source_value = _flow_path_lookup(source.response_json, source_path)
    if source_value is _FLOW_PATH_MISSING:
        return False
    target_param = _resolve_param_reference(target, link.target_path)
    evidence = link.evidence if isinstance(link.evidence, dict) else {}
    source_leaf = re.sub(r"[^a-z0-9]+", "", source_path.split(".")[-1].casefold())
    target_leaf = re.sub(
        r"[^a-z0-9]+",
        "",
        str((target_param.path if target_param is not None else "") or (target_param.key if target_param is not None else "") or link.target_path).split(".")[-1].casefold(),
    )
    hydration_match = bool(
        evidence.get("kind") == "record_hydration"
        and not isinstance(evidence.get("captured_source_value"), (dict, list, bool))
        and not isinstance(evidence.get("captured_target_value"), (dict, list, bool))
        and str(evidence.get("captured_source_value")).strip()
        == str(evidence.get("captured_target_value")).strip()
        and str(evidence.get("captured_target_value")).strip()
        == str(target_param.value if target_param is not None else "").strip()
    )
    hydration_override = bool(
        evidence.get("kind") == "record_hydration"
        and evidence.get("value_overridden") is True
        and source_leaf == target_leaf
    )
    hydration_empty = bool(
        evidence.get("kind") == "record_hydration"
        and evidence.get("empty_projection") is True
        and source_leaf == target_leaf
    )
    if target_param is None or not (
        _recorded_scalar_values_match(source_value, target_param.value)
        or _composite_values_match(source_value, target_param.value)
        or hydration_match
        or hydration_override
        or hydration_empty
    ):
        return False
    source_action = str(evidence.get("source_action_id") or "")
    target_action = str(evidence.get("target_action_id") or "")
    source_transaction = str((source.source_meta or {}).get("trigger_transaction_id") or "")
    target_transaction = str((target.source_meta or {}).get("trigger_transaction_id") or "")
    causal = bool(
        evidence.get("same_action_chain") is True
        or (source_action and source_action == target_action)
        or (source_transaction and source_transaction == target_transaction)
        or evidence.get("kind") in {
            "response_projection", "request_dependency", "causal_transaction", "explicit_projection",
            "record_hydration",
        }
    )
    separate_observed_operations = bool(
        (source_action and target_action and source_action != target_action)
        or (
            source_transaction
            and target_transaction
            and source_transaction != target_transaction
        )
    )
    source_leaf = re.sub(r"[^a-z0-9]+", "", source_path.split(".")[-1].casefold())
    target_leaf = re.sub(
        r"[^a-z0-9]+", "", str(target_param.path or target_param.key).split(".")[-1].casefold(),
    )
    captured_match = evidence.get("captured_value_match")
    stable_identifier_projection = bool(
        link.confirmed
        and float(link.confidence or 0.0) >= 0.95
        and isinstance(captured_match, dict)
        and int(captured_match.get("occurrences") or 0) == 1
        and source_leaf == "id"
        and target_leaf.endswith("id")
    )
    if separate_observed_operations and not (causal or stable_identifier_projection):
        return False
    scalar_envelope_projection = bool(
        source_path in {"data", "result", "value"}
        and not isinstance(source_value, (dict, list))
    )
    structural_projection = bool(
        not _param_has_editable_control_evidence(target_param)
        and (
            source_leaf == target_leaf
            or (
                target_leaf.endswith("id")
                and source_leaf == "id"
            )
            or scalar_envelope_projection
        )
    )
    return causal or stable_identifier_projection or structural_projection


def _prune_unsafe_auto_links(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    kept: list[FlowLink] = []
    for lk in links:
        if (lk.meta or {}).get("unverified_reason") and _link_is_auto_generated(lk):
            continue
        if not _link_is_auto_generated(lk):
            kept.append(lk)
            continue
        if not _auto_link_has_grounded_contract(steps, lk):
            continue
        target = by_id.get(lk.target_step_id)
        param = _resolve_param_reference(target, lk.target_path) if target else None
        if _auto_dependency_link_allowed(param, lk.source_path, lk):
            kept.append(lk)
    links[:] = kept


def _flow_link_kind(link: FlowLink) -> str:
    return str(link.kind or "value")


def _sync_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    _prune_unsafe_auto_links(steps, links)
    by_id = {step.step_id: step for step in steps}
    valid_targets = {
        (lk.link_id, lk.target_step_id, target_param.path)
        for lk in links
        if _flow_link_kind(lk) == "value"
        if (target := by_id.get(lk.target_step_id)) is not None
        if (target_param := _resolve_param_reference(target, lk.target_path)) is not None
    }
    for st in steps:
        for p in st.params:
            if p.source_kind != "previous_response":
                continue
            link_id = p.source.get("link_id")
            if not link_id:
                # An explicitly declared but incomplete response source is an
                # advisory contract problem; do not silently erase it.
                continue
            if (link_id, st.step_id, p.path) in valid_targets:
                continue
            _reset_param_source(p, reason="上游依赖已删除或目标已改变，字段已恢复为用户输入")
    _apply_link_sources(steps, links)


def _merge_flow_read_sources(explicit_reads: list[dict], captured_requests: list[dict], request_roles: list[dict]) -> list[dict]:
    """把录制全量请求里的读响应也作为字段候选源。

    recorder 现在会把 GET/POST 查询放进 captured_requests；字段下拉/选人绑定不能只依赖旧 reads 通道。
    """
    out: list[dict] = []
    merged_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def add(url: str, payload: Any, *, role: str = "", source: dict | None = None,
            sequence: int | None = None) -> None:
        if payload is None:
            return
        source = source or {}
        source_sequence = next((
            source.get(key) for key in ("sequence", "request_index", "index")
            if source.get(key) is not None
        ), sequence)
        source_request_index = next((
            source.get(key) for key in ("request_index", "index")
            if source.get(key) is not None
        ), source_sequence)
        request_id = str(source.get("request_id") or "")
        page_id = str(source.get("page_id") or "")
        frame_id = str(source.get("frame_id") or "")
        payload_fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        # ``reads`` and ``captured_requests`` often contain two projections of
        # the same network request.  Merge only when their immutable request id
        # (or recorder sequence fallback) agrees.  Two identical GET responses
        # observed before and after a write are distinct causal events and must
        # not be collapsed merely because URL/body/page are equal.
        identity = (
            f"request:{request_id}" if request_id
            else f"sequence:{source_sequence}" if source_sequence is not None
            else page_id
        )
        key = (
            url or "",
            payload_fingerprint,
            identity,
            "" if request_id or source_sequence is not None else frame_id,
        )
        existing = merged_by_key.get(key)
        incoming = {
            "url": url or "",
            "json": payload,
            "role": role or "",
            "page_id": page_id,
            "frame_id": frame_id,
            "trigger_action_id": str(source.get("trigger_action_id") or source.get("action_id") or ""),
            "trigger_transaction_id": str(source.get("trigger_transaction_id") or ""),
            "request_id": request_id,
            "request_index": source_request_index,
            "sequence": source_sequence,
            "path": _request_path(source) if source else _request_path({"url": url}),
        }
        if existing is not None:
            # The captured-request projection carries the classifier result and
            # action/transaction anchors that the lightweight response-read
            # projection may lack.  Fill/replace metadata without duplicating
            # the response payload.
            for field in (
                "role", "page_id", "frame_id", "trigger_action_id",
                "trigger_transaction_id", "request_id", "request_index",
                "sequence", "path",
            ):
                value = incoming.get(field)
                if value not in (None, ""):
                    existing[field] = value
            return
        merged_by_key[key] = incoming
        out.append(incoming)

    for r in explicit_reads or []:
        add(
            r.get("url") or "",
            r.get("json", r.get("response_json")),
            role=str(r.get("role") or r.get("request_role") or "explicit_read_option"),
            source=r,
        )
    for sequence, (req, role) in enumerate(zip(captured_requests or [], request_roles or [])):
        payload = req.get("response_json", req.get("json"))
        is_reference_read = (
            str(req.get("method") or "GET").upper() in {"GET", "HEAD"}
            and _list_payload_has_reference_contract(payload)
        )
        if (
            role.get("role") not in {"read_option", "read_context", "business_get"}
            and not is_reference_read
        ):
            continue
        add(
            req.get("url") or "",
            payload,
            role=str(role.get("role") or ""),
            source=req,
            sequence=sequence,
        )
    return out


def _previous_response_source_step_id(param: ParamField) -> str:
    if param.source_kind != "previous_response":
        return ""
    source = dict(param.source or {})
    return str(source.get("step_id") or source.get("source_step_id") or "")


def _dependency_closure_step_ids(spec: FlowSpec, target_ids: set[str]) -> set[str]:
    keep = set(target_ids)
    changed = True
    while changed:
        changed = False
        for link in spec.links or []:
            if link.target_step_id in keep and link.source_step_id and link.source_step_id not in keep:
                keep.add(link.source_step_id)
                changed = True
    return keep


def _dependency_sig(source_step_id: str, source_path: str, target_step_id: str, target_path: str) -> str:
    raw = "|".join([source_step_id or "", source_path or "", target_step_id or "", target_path or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _dependency_match_score(param: ParamField, source_path: str) -> int:
    def token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    source_full = token(source_path)
    source_leaf = token(re.split(r"\.|\[\d+\]", str(source_path or ""))[-1])
    target_tokens = {
        token(param.key),
        token(param.label),
        token(re.split(r"\.|\[\d+\]", str(param.path or ""))[-1]),
    } - {""}
    score = 0
    for target in target_tokens:
        if source_leaf and target == source_leaf:
            score = max(score, 50)
        elif target == source_full:
            score = max(score, 45)
        elif len(target) >= 4 and (target in source_full or (source_leaf and source_leaf in target)):
            score = max(score, 30)
    if "[" not in source_path:
        score += 3
    return score


def _skip_auto_dependency_target(param: ParamField | None) -> bool:
    return not _auto_dependency_target_allowed(param)


def _rejected_dependency_sigs(spec: FlowSpec) -> set[str]:
    meta = spec.meta or {}
    return {str((x.get("sig") if isinstance(x, dict) else x) or x) for x in (meta.get("rejected_dependencies") or [])}


def _record_rejected_dependency(spec: FlowSpec, link: FlowLink) -> None:
    _record_rejected_dependency_raw(
        spec,
        source_step_id=link.source_step_id,
        source_path=link.source_path,
        target_step_id=link.target_step_id,
        target_path=link.target_path,
    )


def _record_rejected_dependency_raw(
    spec: FlowSpec,
    *,
    source_step_id: str,
    source_path: str,
    target_step_id: str,
    target_path: str,
) -> None:
    sig = _dependency_sig(source_step_id, source_path, target_step_id, target_path)
    rejected = list((spec.meta or {}).get("rejected_dependencies") or [])
    if not any(str((x.get("sig") if isinstance(x, dict) else x) or x) == sig for x in rejected):
        rejected.append({
            "sig": sig,
            "source_step_id": source_step_id,
            "source_path": source_path,
            "target_step_id": target_step_id,
            "target_path": target_path,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        })
    spec.meta = {**(spec.meta or {}), "rejected_dependencies": rejected}


def rebuild_flow_dependencies(spec: FlowSpec) -> int:
    """基于已物化步骤重建高置信值驱动依赖。

    只追加缺失候选；不会修改原始 RequestFacts，也不会恢复用户已删除的依赖。
    """
    existing = {
        _dependency_sig(lk.source_step_id, lk.source_path, lk.target_step_id, lk.target_path)
        for lk in spec.links
    }
    rejected = _rejected_dependency_sigs(spec)
    added = 0
    for tgt_idx, target in enumerate(spec.steps):
        if not target.params:
            continue
        for param in target.params:
            if param.locked:
                continue
            target_leaf = re.sub(
                r"[^a-z0-9]+", "", str(param.path or param.key or "").split(".")[-1].lower()
            )
            internal_id_target = target_leaf.endswith("id") and not _looks_user_entered_business_field(param.key, param.path)
            response_owned_candidate = bool(
                not _param_has_editable_control_evidence(param)
                and param.source_kind not in _OPTION_SOURCE_KINDS
                and param.source_kind not in {"user_input", "current_user", "system_time", "computed", "page_context"}
            )
            if _skip_auto_dependency_target(param) and not internal_id_target and not response_owned_candidate:
                continue
            if param.source_kind == "previous_response" and param.source.get("step_id"):
                continue
            value = str(param.value if param.value is not None else "").strip()
            if not value:
                continue
            short_value = len(value) < 4
            matches: list[tuple[FlowStep, str]] = []
            for source in spec.steps[:tgt_idx]:
                if source.response_json is None:
                    continue
                for path, _tokens, leaf_value, _raw in _leaf_paths(source.response_json):
                    if str(leaf_value) == value:
                        matches.append((source, path))
            if len(matches) == 1:
                source, source_path = matches[0]
            else:
                ranked = sorted(
                    [(_dependency_match_score(param, path), source, path) for source, path in matches],
                    key=lambda item: item[0],
                    reverse=True,
                )
                if not ranked or ranked[0][0] < 12:
                    continue
                # 多个响应携带同一值时，字段名仅略相似不足以建立依赖；必须有明显
                # 语义优势，避免 status/id/date 等常见值在不同接口间随机串线。
                if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 8:
                    continue
                _score, source, source_path = ranked[0]
            if "[" in str(source_path or ""):
                continue
            source_leaf = re.sub(r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower())
            semantic_score = _dependency_match_score(param, source_path)
            strong_internal_id = internal_id_target and source_leaf == "id" and len(matches) == 1
            strong_semantic_response = response_owned_candidate and semantic_score >= 40
            if short_value and not (strong_internal_id or strong_semantic_response):
                continue
            if not strong_internal_id and not strong_semantic_response and not _auto_dependency_link_allowed(param, source_path):
                continue
            sig = _dependency_sig(source.step_id, source_path, target.step_id, param.path)
            if sig in existing or sig in rejected:
                continue
            spec.links.append(FlowLink(
                source_step_id=source.step_id,
                source_path=source_path,
                target_step_id=target.step_id,
                target_path=param.path,
                param_name=param.key,
                confirmed=True,
                confidence=0.97,
                reason="promote 后重建依赖：目标字段录制值唯一命中上游响应字段，自动确认为运行期依赖",
                evidence={"kind": "value_match", "value": value, "path_score": semantic_score, "auto_rebuilt": True, "actor": "heuristic"},
                meta={"actor": "heuristic", "verified": False},
            ))
            existing.add(sig)
            added += 1
    # Always prune/synchronize existing links. Previously this only ran when a
    # new dependency was added, so a bad persisted list[0] link survived every
    # later re-analysis and kept the target field hidden as previous_response.
    _sync_link_sources(spec.steps, spec.links)
    return added


_PENDING_FLOW_SPEC_HELPERS = {
    "_FLOW_PATH_MISSING": "dano.execution.page.flow_spec_core.normalization",
    "_OPTION_SOURCE_KINDS": "dano.execution.page.flow_materialization.field_contracts.option_projection",
    "_apply_link_sources": "dano.execution.page.flow_spec_core.controlled_edits",
    "_composite_values_match": "dano.execution.page.flow_materialization.field_contracts.option_projection",
    "_flow_path_lookup": "dano.execution.page.flow_spec_core.normalization",
    "_looks_system_const_field": "dano.execution.page.flow_materialization.field_contracts.common",
    "_looks_user_entered_business_field": "dano.execution.page.flow_materialization.field_contracts.common",
    "_param_has_editable_control_evidence": "dano.execution.page.flow_materialization.field_contracts.caller_ownership",
    "_recorded_scalar_values_match": "dano.execution.page.flow_materialization.field_contracts.option_projection",
    "_reset_param_source": "dano.execution.page.flow_spec_core.controlled_edits",
    "_resolve_param_reference": "dano.execution.page.flow_spec_core.controlled_edits",
}


def _bind_flow_spec_helpers() -> None:
    import sys

    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
