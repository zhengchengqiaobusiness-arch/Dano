"""Stage 8: release candidate and publish-payload preparation."""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
import hashlib
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFacts,
    ReviewItem,
    SelectBinding,
)
from dano.execution.page.request_capture import (
    _fact_path_tokens,
    as_list_payload,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _OPTION_SOURCE_KINDS,
    _enum_label_value,
    _enum_option_map_from_options,
    _explicit_enum_value_map,
)
from dano.execution.page.capability_contracts import (
    _ensure_capability_explanations,
    _ensure_external_transform_relations,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _flow_fingerprint,
)
from dano.execution.page.flow_materialization.response_maps import (
    _materialize_captured_response_key_maps,
)
from dano.execution.page.flow_materialization.field_contracts.option_sync import (
    _refresh_api_option_display_labels,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _repair_readonly_control_defaults,
)
from dano.execution.page.flow_materialization.field_contracts.option_repair import (
    _repair_structural_option_bindings,
)
from dano.execution.page.flow_materialization.field_contracts.create_form import (
    _repair_uncontrolled_write_state_fields,
)
from dano.execution.page.recording_facts import (
    _request_path,
    _request_sequence_value,
)
from dano.execution.page.flow_materialization.links import (
    _sync_link_sources,
)
from dano.execution.page.flow_spec_core.serialization import (
    flow_spec_release_payload,
)
from dano.execution.page.flow_spec_core.request_contract import (
    flow_spec_to_api_request,
)


_PUBLISH_BLOCKING_REVIEW_TYPES = frozenset({
    "system_const_exposed",
    "broken_link",
    "link_source_missing",
    "link_target_missing",
    "link_confirmation",
})


def _runtime_param_publish_error(param: ParamField) -> str | None:
    """Source inference/configuration is advisory and never a publish error.

    The same field-local finding is exposed by ``build_review_items`` and
    ``_field_source_configuration_advice``.  Keeping this compatibility helper
    returning ``None`` prevents source heuristics from entering request-builder
    errors while preserving hard failures elsewhere (missing request body,
    malformed request data, absent executable steps, and so on).
    """
    return None


def _diagnostic_publish_findings(spec: FlowSpec) -> tuple[list[str], list[str]]:
    """录制期诊断事实进入发布校验。

    只把能关联到已选业务步骤的 requestfailed 升级为 error；pageerror/console error
    先作为 warning，避免第三方脚本噪声误拦发布。
    """
    errors: list[str] = []
    warnings: list[str] = []
    diagnostics = list(spec.diagnostics or (spec.meta or {}).get("diagnostics") or [])
    if not diagnostics:
        return errors, warnings
    kept_request_indices = {
        st.source_meta.get("request_index")
        for st in spec.steps
        if st.source_meta.get("request_index") is not None
    }
    kept_urls = {str(st.url or "") for st in spec.steps if st.url}
    for d in diagnostics:
        kind = str(d.get("type") or "")
        msg = str(d.get("message") or "").strip()
        url = str(d.get("url") or "")
        req_idx = d.get("request_index")
        detail = msg or url or kind
        # Playwright 页面切换、录制结束或目标服务主动断开连接时，浏览器控制台常会
        # 留下 ERR_CONNECTION_CLOSED/ERR_ABORTED。若它没有关联到已纳入的业务请求，
        # 这只是录制环境噪声，不应成为 Skill 流程问题。
        benign_disconnect = bool(re.search(
            r"ERR_(?:CONNECTION_CLOSED|ABORTED|CANCELED)|Target page, context or browser has been closed",
            detail,
            re.I,
        )) and req_idx not in kept_request_indices and url not in kept_urls
        if benign_disconnect:
            continue
        if kind == "requestfailed" and (req_idx in kept_request_indices or url in kept_urls):
            errors.append(f"录制期业务请求失败: {detail[:200]}")
        elif kind == "pageerror":
            warnings.append(f"录制期页面异常: {detail[:200]}")
        elif kind == "console" and str(d.get("level") or "").lower() == "error":
            warnings.append(f"录制期控制台错误: {detail[:200]}")
    return errors, warnings


def _enum_map_covers_recorded_value(param: ParamField) -> bool:
    """枚举字段当前提交值是否能由候选 label 映射出来。

    body 存显示名时(label 本身等于 value)天然通过；body 存短码(type=2)时,必须有
    enum_value_map 或 {label,value} 能把某个显示项映射到 2,否则导出的 skill 会让前端传名字、
    运行时却提交不了真实短码。
    """
    current = str(param.value or "").strip()
    if not current:
        return True
    labels: list[str] = []
    option_values: list[Any] = []
    for opt in param.enum_options or []:
        pair = _enum_label_value(opt)
        if not pair:
            continue
        label, value = pair
        labels.append(label)
        option_values.append(value)
    explicit = _explicit_enum_value_map(param.enum_options, param.enum_value_map)
    if param.source_kind in {"page_enum", "manual_enum"}:
        if not labels or not all(label in explicit and explicit[label] is not None for label in labels):
            return False
        mapped_values = list(explicit.values())
    else:
        mapped_values = list(explicit.values()) or option_values
    return any(str(v) == current for v in mapped_values if v not in (None, ""))


def _incomplete_page_enum_is_executable(param: ParamField) -> bool:
    """Whether a partial DOM snapshot still defines a safe captured domain.

    When the request submits display text directly, captured labels are valid
    wire values and a partial list is quality advice only. When the request uses
    an ID/code, *every displayed candidate in the snapshot* needs an explicit
    mapping; knowing only the currently selected pair is insufficient because a
    caller could choose another label and submit it as the wire value.
    """
    labels = [
        pair[0] for pair in (_enum_label_value(item) for item in (param.enum_options or []))
        if pair is not None
    ]
    if not labels:
        return False
    explicit = _explicit_enum_value_map(param.enum_options, param.enum_value_map)
    if not explicit or not all(label in explicit and explicit[label] is not None for label in labels):
        return False
    current = str(param.value or "").strip()
    return not current or any(str(value) == current for value in explicit.values())


def _manual_enum_mapping_complete(param: ParamField) -> bool:
    """Whether every manually maintained label has an explicit wire value.

    Bare strings from a client-side textarea are deliberately *not* treated as
    ``label == value``.  That identity assumption is valid only when grounded by
    a page control/request pair; accepting it for ``manual_enum`` would let a
    client turn display names into fake API values merely by toggling confirmed.
    Explicit ``{label, value}``, two-item pairs, or ``enum_value_map`` entries are
    accepted, including legitimate identity mappings intentionally entered by an
    operator.
    """
    options = list(param.enum_options or [])
    if not options:
        return False
    explicit = dict(param.enum_value_map or {})
    labels: list[str] = []
    for option in options:
        if isinstance(option, dict):
            label = option.get("label", option.get("name", option.get("text")))
            if label in (None, ""):
                return False
            labels.append(str(label))
            if "value" in option and option.get("value") is not None:
                explicit.setdefault(str(label), option.get("value"))
        elif isinstance(option, (list, tuple)) and len(option) >= 2:
            label, value = option[0], option[1]
            if label in (None, "") or value is None:
                return False
            labels.append(str(label))
            explicit.setdefault(str(label), value)
        else:
            label = str(option or "").strip()
            if not label:
                return False
            labels.append(label)
    return bool(labels) and all(label in explicit and explicit[label] is not None for label in labels)


_VALUE_ONLY_LABEL_RE = re.compile(
    r"^\s*(?:[-+]?\d+(?:\.\d+)?|[0-9a-f]{8,}|[A-Za-z]{0,4}[-_]?\d{3,}|[A-Za-z0-9_-]{12,})\s*$",
    re.I,
)


def _enum_options_look_value_only(param: ParamField) -> bool:
    """候选全是 1/2/3、长 ID、短码且没有非等值映射时,说明把内部值当成了显示名。"""
    pairs = [p for p in (_enum_label_value(o) for o in (param.enum_options or [])) if p]
    if not pairs:
        return False
    labels = [label for label, _value in pairs]
    if not all(_VALUE_ONLY_LABEL_RE.match(label) for label in labels):
        return False
    value_map = dict(param.enum_value_map or _enum_option_map_from_options(param.enum_options))
    if not value_map:
        return True
    # 如果至少有一个「人类显示名 -> 内部值」的非等值映射,就不是坏枚举。
    return not any(
        label and not _VALUE_ONLY_LABEL_RE.match(label) and str(value) != str(label)
        for label, value in value_map.items()
    )


_INTERNAL_EXPOSED_PATH_RE = re.compile(
    r"(^|[.\]])[A-Za-z0-9_]*(?:id|ids|code|dm|lx|sf|flag|state|status|type)$",
    re.I,
)


def _select_has_executable_options(sel: SelectBinding | None) -> bool:
    if sel is None:
        return False
    return bool(
        (sel.source_url and (sel.value_key or sel.option_map or sel.options))
        or sel.options
        or sel.option_map
    )


def _param_looks_exposed_internal_value(param: ParamField) -> bool:
    """内部 ID/短码/空 id 不应作为普通用户输入暴露。"""
    if param.category != "user_param" or not param.exposed_to_user:
        return False
    if (
        param.source_kind in _OPTION_SOURCE_KINDS
        and bool(param.enum_value_map or param.enum_options)
        and _enum_map_covers_recorded_value(param)
    ):
        # 调用方看到的是业务 label，运行期才映射为内部 ID；这正是正确的枚举契约。
        return False
    if param.source_kind not in {"user_input", "unknown", "api_option"}:
        return False
    path_key = f"{param.path}.{param.key}"
    if not (_INTERNAL_EXPOSED_PATH_RE.search(str(param.path or "")) or _INTERNAL_EXPOSED_PATH_RE.search(str(param.key or ""))):
        return False
    value = str(param.value or "").strip()
    if value == "":
        return True
    if param.type in {"number", "boolean"} and not re.search(r"(id|code|dm|lx|sf|flag|state|status|type)", path_key, re.I):
        return False
    return bool(_VALUE_ONLY_LABEL_RE.match(value) or re.match(r"^[A-Z]{1,6}$", value))


def _publish_issue_groups(errors: list[str], warnings: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Expose only request-construction failures; semantic findings are suggestions."""
    entries: list[dict[str, Any]] = []
    for severity, messages in (("error", errors), ("warning", warnings)):
        for message in dict.fromkeys(str(item) for item in messages if item):
            digest = hashlib.sha1(message.encode("utf-8")).hexdigest()[:12]
            entries.append({
                "severity": severity,
                "message": message,
                "source": "request_builder",
                "target": {"kind": "flow"},
                "blocking": severity == "error",
                "audience": "operator",
                "actionable": True,
                "auto_fixable": False,
                "code": f"request_builder_{digest}",
                "issue_id": f"publish:request_builder:{digest}",
            })
    return {"execution": entries} if entries else {}


def _field_source_review_issues(review_items: list[ReviewItem]) -> list[dict[str, Any]]:
    """Project unresolved field-source advice into the operator warning list.

    This is deliberately separate from request-builder failures: an unknown
    source is useful, field-local review context, but it is not proof that the
    operator's type/category/source combination is invalid.
    """
    issues: list[dict[str, Any]] = []
    for item in review_items:
        # "Unknown" is already visible on the field card and has no concrete
        # action. Repeating a long generic explanation in the status panel only
        # creates noise. Keep only explicitly selected but incomplete sources.
        if item.type != "field_source_incomplete" or item.resolved:
            continue
        issues.append({
            "severity": "warning",
            "message": f"{item.title}：{item.reason}" if item.reason else item.title,
            "source": "review",
            "target": dict(item.target or {}),
            "blocking": False,
            "ignorable": True,
            "audience": "operator",
            "actionable": True,
            "auto_fixable": False,
            "code": item.type,
            "issue_id": f"review:{item.id}",
            "review_id": item.id,
            "suggested_action": item.suggested_action,
        })
    return issues


def _enum_mapping_issues(steps: list[FlowStep]) -> list[dict[str, Any]]:
    """Expose locatable warnings only for mappings inferred from the page.

    Manual enums are operator-authored contract advice and already remain in
    ``suggestions``. Promoting them again into ``issue_groups`` made generated
    advice look like a newly detected recording defect.
    """
    issues: list[dict[str, Any]] = []
    for step in steps:
        for param in step.params:
            if (
                param.type not in {"enum", "list-enum"}
                or not param.enum_options
                or param.source_kind != "page_enum"
            ):
                continue
            explicit = _explicit_enum_value_map(param.enum_options, param.enum_value_map)
            labels = list(dict.fromkeys(
                pair[0]
                for pair in (_enum_label_value(option) for option in param.enum_options)
                if pair is not None
            ))
            missing = [
                label for label in labels
                if label not in explicit or explicit[label] is None
            ]
            if not missing:
                continue
            path = param.path or param.key
            digest = hashlib.sha1(f"{step.step_id}:{path}".encode("utf-8")).hexdigest()[:12]
            issues.append({
                "severity": "warning",
                "message": f"枚举字段 `{param.key or path}` 存在未映射值：{'、'.join(missing)}",
                "source": "enum_mapping",
                "target": {"kind": "param", "step_id": step.step_id, "path": path},
                "blocking": False,
                "audience": "operator",
                "actionable": True,
                "auto_fixable": False,
                "code": "enum_mapping_missing",
                "issue_id": f"enum_mapping:{digest}",
            })
    return issues


def _compiled_contract_issue_groups(
    spec: FlowSpec,
    api_request: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    resolved_review_ids: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert late compiled-contract advice into locatable workbench issues."""
    groups: dict[str, list[dict[str, Any]]] = {}
    resolved_review_ids = resolved_review_ids or set()
    compiled_steps = list(api_request.get("steps") or [api_request])
    by_step_id = {step.step_id: step for step in spec.steps}
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "compiled_contract")
        if kind in {"self_check", "session_constant"}:
            continue
        target: dict[str, Any] = {}
        group = "flow"
        step_index = finding.get("step")
        compiled_step = (
            compiled_steps[step_index]
            if isinstance(step_index, int) and 0 <= step_index < len(compiled_steps)
            else {}
        )
        step_id = str((compiled_step or {}).get("step_id") or "")
        if kind == "placeholder_name":
            param_name = str(finding.get("param") or "")
            step = by_step_id.get(step_id)
            param = next((
                item for item in (step.params if step else [])
                if param_name in {item.key, item.label, item.path}
            ), None)
            target = {
                "kind": "param",
                "step_id": step_id,
                "path": param.path if param is not None else param_name,
                "key": param.key if param is not None else param_name,
            }
            group = "field"
        elif kind.startswith("capability_"):
            cap_ref = str(finding.get("capability") or "")
            field_name = str(finding.get("field") or "")
            target = {
                "kind": "capability_output" if "output" in kind else "capability",
                "capability": cap_ref,
                **({"field": field_name} if field_name else {}),
            }
            group = "capability"
        else:
            target = {"kind": "flow"}
        review_id = _review_id(f"compiled_{kind}", target)
        if review_id in resolved_review_ids:
            continue
        issue = {
            "severity": "warning",
            "message": str(finding.get("detail") or finding.get("message") or kind),
            "source": "review",
            "target": {key: value for key, value in target.items() if value not in (None, "")},
            "blocking": False,
            "ignorable": True,
            "audience": "operator",
            "actionable": True,
            "auto_fixable": False,
            "code": kind,
            "issue_id": f"review:{review_id}",
            "review_id": review_id,
        }
        groups.setdefault(group, []).append(issue)
    return groups


def _compiled_contract_review_items(
    spec: FlowSpec,
    *,
    prepared: bool = False,
) -> list[ReviewItem]:
    """Materialize unresolved compiled-contract advice as stable ReviewItems.

    Publish validation operates on a compiled ``api_request`` while ignore state
    lives in ``FlowSpec.review_items``.  Keeping these findings in both forms is
    what makes an operator dismissal survive the next prepare/validate cycle.
    """
    if not spec.capabilities:
        return []
    api_request, build_errors = flow_spec_to_api_request(spec, _prepared=prepared)
    if api_request is None or build_errors:
        return []
    from dano.execution.page.repair_ops import collect_repair_findings

    groups = _compiled_contract_issue_groups(
        spec,
        api_request,
        collect_repair_findings(api_request),
    )
    items: list[ReviewItem] = []
    for issues in groups.values():
        for issue in issues:
            message = str(issue.get("message") or "待确认的编译契约建议")
            items.append(ReviewItem(
                id=str(issue["review_id"]),
                type=f"compiled_{issue.get('code') or 'contract'}",
                severity="medium",
                title=message,
                target=dict(issue.get("target") or {}),
                current_guess="compiled_contract",
                suggested_action="review_compiled_contract",
                reason=message,
                blocking=False,
                ignorable=True,
            ))
    return items


def _generated_review_items(spec: FlowSpec, *, prepared: bool = False) -> list[ReviewItem]:
    """Build every generated review item with one stable-ID dedupe pass."""
    generated = [
        *build_review_items(spec),
        *_compiled_contract_review_items(spec, prepared=prepared),
    ]
    deduped: dict[str, ReviewItem] = {}
    for item in generated:
        existing = deduped.get(item.id)
        if existing is None or _severity_rank(item.severity) > _severity_rank(existing.severity):
            deduped[item.id] = item
    return list(deduped.values())


def _legacy_fact_check_is_grounded(spec: FlowSpec, step: FlowStep, fact_check: dict) -> bool:
    """Revalidate persisted checks against immutable request facts."""
    if not fact_check or (step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
        return False
    facts = list((spec.request_facts or RequestFacts()).requests or [])
    if not facts:
        return False
    meta = step.source_meta or {}
    write_id = str(meta.get("request_id") or "")
    write_seq = _request_sequence_value(meta.get("sequence", meta.get("request_index")))
    write_fact = next((fact for fact in facts if write_id and fact.request_id == write_id), None)
    if write_fact is None and write_seq is not None:
        write_fact = next((fact for fact in facts if _request_sequence_value(fact.sequence) == write_seq), None)
    if write_fact is None:
        return False
    write_seq = _request_sequence_value(write_fact.sequence)
    if write_seq is None:
        return False

    endpoint_path = _request_path({"url": str(fact_check.get("endpoint") or "")})
    read_facts = [
        fact for fact in facts
        if _request_path({"url": fact.url or fact.path}) == endpoint_path
        and (_request_sequence_value(fact.sequence) or -1) > write_seq
        and str(((spec.request_facts.analysis or {}).get(fact.request_id) or RequestAnalysis()).role) == "business_get"
    ]
    if len(read_facts) != 1:
        return False
    read_fact = read_facts[0]
    write_tx = str(getattr(write_fact, "trigger_transaction_id", "") or "")
    read_tx = str(getattr(read_fact, "trigger_transaction_id", "") or "")
    if not (
        (write_tx and read_tx and write_tx == read_tx)
        or (_fact_path_tokens(write_fact.url or write_fact.path) & _fact_path_tokens(read_fact.url or read_fact.path))
    ):
        return False

    param_name = str(fact_check.get("param") or "")
    param = next((item for item in step.params if item.key == param_name), None)
    value = (step.sample_inputs or {}).get(param_name)
    if value in (None, "") and param is not None:
        value = param.value
    if param is None or value in (None, ""):
        return False
    match_field = str(fact_check.get("match_field") or "")
    matches = [
        item for item in (as_list_payload(read_fact.response_json) or [])
        if isinstance(item, dict) and match_field in item and str(item.get(match_field)) == str(value)
    ]
    return len(matches) == 1


def _executor_fact_check_is_verified(spec: FlowSpec, fact_check: dict) -> bool:
    """Executor-verified checks carry the verification_id minted by the write/read replay."""
    if fact_check.get("verified") is not True:
        return False
    verification_id = str(fact_check.get("verification_id") or "")
    if not verification_id:
        return False
    log = list((spec.meta or {}).get("verification_log") or [])
    if not log:
        # The op-level guard (`bind_verify_read`) already validated the record
        # when it was applied; the log may have been dropped by projections.
        return True
    from dano.execution.page.verification_log import find_verification

    record = find_verification(verification_id, log)
    if record is None:
        return False
    return record.get("status") == "passed"


def _prune_invalid_fact_checks(spec: FlowSpec) -> None:
    for step in spec.steps:
        if not step.fact_check:
            continue
        if _executor_fact_check_is_verified(spec, step.fact_check):
            continue
        if not _legacy_fact_check_is_grounded(spec, step, step.fact_check):
            step.fact_check = None


def prepare_flow_spec_for_publish(spec: FlowSpec) -> FlowSpec:
    """Canonicalize the current workbench state without invoking the Pi Agent."""
    current = sync_flow_spec_models(spec.model_copy(deep=True))
    _repair_structural_option_bindings(current)
    _refresh_api_option_display_labels(current)
    _apply_mechanical_field_contracts(current)
    _repair_readonly_control_defaults(current)
    _repair_uncontrolled_write_state_fields(current)
    _materialize_captured_response_key_maps(
        current.steps,
        current.links,
        [fact.model_dump(exclude_none=True) for fact in current.request_facts.requests],
    )
    _sync_link_sources(current.steps, current.links)
    by_step_id = {step.step_id: step for step in current.steps}
    public_anchor_ids = set(_public_capability_anchor_step_ids(current))
    for capability in current.capabilities:
        changed = True
        while changed:
            changed = False
            member_ids = set(_capability_node_step_ids(capability))
            for link in executable_flow_links(current):
                source = by_step_id.get(link.source_step_id)
                if (
                    link.target_step_id in member_ids
                    and link.source_step_id not in member_ids
                    and link.source_step_id not in public_anchor_ids
                    and source is not None
                    and not _is_write_step(source)
                ):
                    _add_step_id_to_capability(
                        current, capability, link.source_step_id,
                    )
                    changed = link.source_step_id in set(
                        _capability_node_step_ids(capability)
                    )
        _sync_capability_order(current, capability)
    _prune_invalid_fact_checks(current)
    _canonicalize_public_capability_identities(current)
    _normalize_capability_references(current)
    current = _ensure_capability_explanations(
        current,
        ((current.meta or {}).get("capability_model") or {}).get("semantic_plan") or {},
    )
    current = _ensure_external_transform_relations(_sync_capability_io_schemas(current))
    current = ensure_recorded_goal(current)
    # Verification and canonical schema projection can add trusted, derived
    # contract details after the planner originally accepted a capability.
    # Refresh only machine-owned confirmations on that final canonical shape;
    # user-owned/locked confirmations must continue to detect later edits.
    if bool(((current.meta or {}).get("verification_run") or {}).get("complete")):
        for cap in current.capabilities or []:
            if (
                cap.confirmed
                and not cap.locked
                and cap.updated_by in {"planner", "repair", "agent", "system"}
            ):
                cap.confirmation_hash = _capability_confirmation_hash(
                    current, cap, prepared=True,
                )
    return current


def prepare_flow_release_candidate(spec: FlowSpec) -> tuple[FlowSpec, dict[str, Any]]:
    """Freeze the exact canonical workbench contract consumed by publish/export."""
    current = prepare_flow_spec_for_publish(spec)
    # The release is persisted as JSON and reconstructed before its Pi review
    # is consumed.  Freeze that exact round-tripped model *before* computing
    # the fingerprint.  Previously ``_flow_fingerprint`` normalised a private
    # copy but this function returned the pre-normalised ``current`` object;
    # after a manual workbench edit the review therefore hashed one model while
    # the draft stored another.
    current = FlowSpec.model_validate(flow_spec_release_payload(current))
    fingerprint = _flow_fingerprint(current)
    inventory = [
        {
            "capability_id": cap.capability_id,
            "name": cap.name,
            "kind": cap.kind,
            "step_ids": list(_capability_node_step_ids(cap)),
            "memberships": [
                {
                    "step_id": ref.step_id,
                    "request_id": ref.request_id,
                    "usage": ref.usage,
                    "origin": ref.origin,
                }
                for ref in (cap.request_refs or [])
            ],
        }
        for cap in current.capabilities or []
    ]
    release = {
        "protocol": "dano.recording_release.v1",
        "release_id": f"{current.flow_id}-{fingerprint}",
        "flow_fingerprint": fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "interface_inventory": inventory,
    }
    current.meta = {**(current.meta or {}), "release_candidate": release}
    # Keep this invariant next to the only release-freezing function.  A future
    # schema migration or derived-model synchroniser must fail here, before Pi
    # review and draft creation, rather than surface as a misleading publish
    # error after the operator has already waited for review.
    frozen = flow_spec_release_payload(current)
    frozen_fingerprint = _flow_fingerprint(FlowSpec.model_validate(frozen))
    if frozen_fingerprint != fingerprint:
        raise ValueError(
            "FlowSpec release snapshot is not serialization-stable: "
            f"{fingerprint} != {frozen_fingerprint}"
        )
    return current, release


_PENDING_FLOW_SPEC_HELPERS = ('_apply_mechanical_field_contracts', '_review_id', '_severity_rank', 'build_review_items', 'ensure_recorded_goal', 'sync_flow_spec_models', '_add_step_id_to_capability', '_canonicalize_public_capability_identities', '_capability_confirmation_hash', '_capability_node_step_ids', '_is_write_step', '_normalize_capability_references', '_public_capability_anchor_step_ids', '_sync_capability_io_schemas', '_sync_capability_order', 'executable_flow_links',)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
