"""Stage 5: arithmetic and date-span computed field contracts."""
from __future__ import annotations

from typing import Any
from datetime import datetime
import json
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _OPTION_SOURCE_KINDS,
)
from dano.execution.page.recording_facts import (
    _field_leaf_token,
    _is_document_record_identity_path,
    _looks_pagination_field,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _param_control_is_readonly,
    _param_control_kinds,
    _param_field_manually_edited,
    _param_group_prefix,
    _param_source_agent_classified,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_has_editable_control_evidence,
    _param_was_caller_typed,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _param_is_document_record_identity,
)
from dano.execution.page.flow_materialization.request_steps import (
    _step_sequence,
)


def _timestamp_is_near_request(value: Any, request: dict[str, Any] | None) -> bool:
    """True only when a captured timestamp is the request's own 'now'.

    Edit hydration reuses the record's create/update time. Treating that as
    ``now_ms`` because the field is named createTime overwrites the upstream
    value at replay.
    """
    actual = _date_like_epoch_seconds(value)
    if actual is None or not isinstance(request, dict):
        return False
    captured = _date_like_epoch_seconds(
        request.get("timestamp") or request.get("captured_at") or request.get("observed_at")
    )
    if captured is None:
        return False
    return abs(actual - captured) <= 120.0


def _date_like_epoch_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return number / 1000.0 if abs(number) >= 10**11 else number
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _as_finite_number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _numbers_match(expected: float, actual: float) -> bool:
    return abs(expected - actual) <= max(0.02, abs(expected) * 1e-6)


_ARITHMETIC_STRATEGIES: tuple[tuple[str, Any, bool], ...] = (
    ("product", lambda left, right: left * right, True),
    ("sum", lambda left, right: left + right, True),
    ("difference", lambda left, right: left - right, False),
    ("percent_of", lambda left, right: left * right / 100.0, False),
    ("remainder_after_percent", lambda left, right: left * (1.0 - right / 100.0), False),
)


_IDENTITY_ARITHMETIC_EPS = 1e-9


_INPUT_OPERAND_KINDS = frozenset({
    "selected_option_field", "user_input",
})


_STABLE_OPERAND_KINDS = _INPUT_OPERAND_KINDS | frozenset({
    "computed", "previous_response", "page_default", "page_rule",
})


def _looks_non_quantity_formula_leaf(key: str, path: str) -> bool:
    """IDs, codes and state discriminators can numerically coincide with money."""
    leaf = _field_leaf_token(key, path)
    if leaf in {
        "id", "ids", "code", "no", "key", "status", "state", "type", "flag",
        "creator", "updater", "modifier", "owner", "assignee", "operator",
    }:
        return True
    return leaf.endswith(("id", "ids", "code", "key", "status", "state"))


def _looks_count_formula_leaf(key: str, path: str) -> bool:
    return _field_leaf_token(key, path) in {"count", "qty", "quantity", "num"}


def _looks_total_formula_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    return any(token in leaf for token in (
        "total", "amount", "subtotal", "payable", "linetotal", "discountprice",
        "taxprice", "taxamount",
    ))


def _looks_unit_price_formula_leaf(key: str, path: str) -> bool:
    """Unit/catalog prices are inputs or row echoes, not formula targets."""
    if _looks_total_formula_leaf(key, path) or _looks_percent_formula_leaf(key, path):
        return False
    leaf = _field_leaf_token(key, path)
    return any(token in leaf for token in ("price", "unitprice", "taxprice", "cost"))


def _looks_percent_formula_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    return any(token in leaf for token in ("percent", "rate", "ratio"))


def _param_is_quantity_or_formula_leaf(key: str, path: str) -> bool:
    """Qty/totals/rates are typed or computed, never option-row echoes."""
    return (
        _looks_count_formula_leaf(key, path)
        or _looks_total_formula_leaf(key, path)
        or _looks_percent_formula_leaf(key, path)
    )


def _is_numeric_formula_operand(param: ParamField) -> bool:
    """Selects, enums and record IDs can numerically coincide; they are not quantities."""
    if _looks_pagination_field(param.key, param.path):
        return False
    if param.source_kind in _OPTION_SOURCE_KINDS or param.type in {"enum", "list-enum"}:
        return False
    if _param_control_kinds(param) & {"select", "combobox", "radio"}:
        return False
    if _is_document_record_identity_path(param.key, param.path):
        return False
    if _looks_non_quantity_formula_leaf(param.key, param.path):
        return False
    if param.source_kind == "previous_response":
        return _as_finite_number(param.value) is not None
    return True


def _is_stable_operand(param: ParamField) -> bool:
    if not _is_numeric_formula_operand(param):
        return False
    if "number" in _param_control_kinds(param):
        return True
    if _param_was_caller_typed(param) or param.source_kind in _STABLE_OPERAND_KINDS:
        return True
    if (
        param.source_kind in {"", "unknown", "page_default"}
        and _as_finite_number(param.value) is not None
    ):
        return True
    return False


def _arithmetic_target_allowed(param: ParamField) -> bool:
    """Formulas hide derived numbers, never caller filters or typed controls."""
    if (
        param.locked
        or _param_was_caller_typed(param)
        or _param_source_agent_classified(param)
    ):
        return False
    if str(param.path or "").startswith("query."):
        return False
    if _looks_pagination_field(param.key, param.path):
        return False
    if _param_is_document_record_identity(param):
        return False
    if _looks_non_quantity_formula_leaf(param.key, param.path):
        return False
    if _looks_unit_price_formula_leaf(param.key, param.path):
        return False
    if param.source_kind in _OPTION_SOURCE_KINDS or param.type in {"enum", "list-enum"}:
        return False
    if _param_control_kinds(param) & {"select", "combobox", "radio"}:
        return False
    if param.source_kind in {
        "computed", "selected_option_field", "current_user",
        "system_time", "system_generated",
    }:
        return False
    if (
        _param_has_editable_control_evidence(param)
        and not _param_control_is_readonly(param)
    ):
        return False
    return True


def _is_identity_arithmetic(kind: str, left: float, right: float) -> bool:
    if kind == "product":
        return (
            abs(left - 1.0) <= _IDENTITY_ARITHMETIC_EPS
            or abs(right - 1.0) <= _IDENTITY_ARITHMETIC_EPS
            or abs(left) <= _IDENTITY_ARITHMETIC_EPS
            or abs(right) <= _IDENTITY_ARITHMETIC_EPS
        )
    if kind == "sum":
        return abs(left) <= _IDENTITY_ARITHMETIC_EPS or abs(right) <= _IDENTITY_ARITHMETIC_EPS
    if kind == "difference":
        return abs(right) <= _IDENTITY_ARITHMETIC_EPS or abs(left - right) <= _IDENTITY_ARITHMETIC_EPS
    if kind == "percent_of":
        return abs(right - 100.0) <= _IDENTITY_ARITHMETIC_EPS
    if kind == "remainder_after_percent":
        return abs(right) <= _IDENTITY_ARITHMETIC_EPS
    return False


def _operand_quality(param: ParamField) -> int:
    return {
        "computed": 4,
        "selected_option_field": 3,
        "user_input": 3,
        "form_option": 3,
        "api_option": 3,
        "page_enum": 3,
        "previous_response": 2,
        "page_default": 2,
        "page_rule": 2,
        "unknown": 1,
    }.get(param.source_kind, 0)


def _identity_product_allowed(target: ParamField, left: ParamField, right: ParamField) -> bool:
    left_number = _as_finite_number(left.value)
    one = (
        left
        if left_number is not None and abs(left_number - 1.0) <= _IDENTITY_ARITHMETIC_EPS
        else right
    )
    other = right if one is left else left
    same_group = (
        _param_group_prefix(one.path) == _param_group_prefix(target.path)
        and _param_group_prefix(other.path) == _param_group_prefix(target.path)
    )
    return (
        same_group
        and not _param_was_caller_typed(target)
        and _looks_count_formula_leaf(one.key, one.path)
        and _looks_total_formula_leaf(target.key, target.path)
        and not _looks_total_formula_leaf(other.key, other.path)
    )


def _arithmetic_match_score(
    target: ParamField,
    kind: str,
    left: ParamField,
    right: ParamField,
    identity: bool,
) -> tuple[int, int, int]:
    same = int(_param_group_prefix(left.path) == _param_group_prefix(target.path)) + int(
        _param_group_prefix(right.path) == _param_group_prefix(target.path)
    )
    return (same, _operand_quality(left) + _operand_quality(right), int(not identity))


def _pick_arithmetic_match(
    target: ParamField,
    target_number: float,
    siblings: list[tuple[ParamField, float]],
) -> tuple[str, ParamField, ParamField] | None:
    matches: list[tuple[str, ParamField, ParamField, bool]] = []
    for left, left_number in siblings:
        for right, right_number in siblings:
            if left is right:
                continue
            for kind, compute, _commutative in _ARITHMETIC_STRATEGIES:
                try:
                    actual = compute(left_number, right_number)
                except ZeroDivisionError:
                    continue
                if not _numbers_match(target_number, actual):
                    continue
                if kind in {"percent_of", "remainder_after_percent"} and not (
                    _looks_percent_formula_leaf(left.key, left.path)
                    or _looks_percent_formula_leaf(right.key, right.path)
                ):
                    continue
                identity = _is_identity_arithmetic(kind, left_number, right_number)
                if identity and not (
                    kind == "product" and _identity_product_allowed(target, left, right)
                ):
                    continue
                if not (_is_stable_operand(left) and _is_stable_operand(right)):
                    continue
                if not (
                    _arithmetic_operand_semantic_ok(left, kind=kind)
                    and _arithmetic_operand_semantic_ok(right, kind=kind)
                ):
                    continue
                matches.append((kind, left, right, identity))
    if not matches:
        return None
    best_score = max(_arithmetic_match_score(target, *item) for item in matches)
    top = [item for item in matches if _arithmetic_match_score(target, *item) == best_score]
    kinds = {item[0] for item in top}
    if len(kinds) != 1:
        percent_top = [
            item for item in top
            if item[0] in {"percent_of", "remainder_after_percent"}
            or _looks_percent_formula_leaf(item[1].key, item[1].path)
            or _looks_percent_formula_leaf(item[2].key, item[2].path)
        ]
        percent_kinds = {item[0] for item in percent_top}
        if len(percent_kinds) != 1:
            return None
        top = percent_top
        kinds = percent_kinds
    kind = next(iter(kinds))

    def pair_key(left: ParamField, right: ParamField) -> tuple[float, float]:
        left_number = round(float(_as_finite_number(left.value) or 0.0), 8)
        right_number = round(float(_as_finite_number(right.value) or 0.0), 8)
        if kind in {"product", "sum"}:
            return (min(left_number, right_number), max(left_number, right_number))
        if kind == "percent_of":
            return (min(left_number, right_number), max(left_number, right_number))
        return (left_number, right_number)

    equivalent = {pair_key(left, right) for _kind, left, right, _identity in top}
    if len(equivalent) != 1:
        return None
    top.sort(key=lambda item: (
        -_operand_quality(item[1]),
        -_operand_quality(item[2]),
        str(item[1].path),
        str(item[2].path),
        str(item[1].key),
        str(item[2].key),
    ))
    _kind, left, right, _identity = top[0]
    if kind == "percent_of":
        left_number = abs(float(_as_finite_number(left.value) or 0.0))
        right_number = abs(float(_as_finite_number(right.value) or 0.0))
        left_is_base = (
            _operand_quality(left) > _operand_quality(right)
            or (
                _operand_quality(left) == _operand_quality(right)
                and left_number > right_number
            )
        )
        if not left_is_base:
            left, right = right, left
    return kind, left, right


def _arithmetic_operand_semantic_ok(param: ParamField, *, kind: str = "") -> bool:
    if _looks_non_quantity_formula_leaf(param.key, param.path):
        return False
    if (
        _looks_count_formula_leaf(param.key, param.path)
        or _looks_unit_price_formula_leaf(param.key, param.path)
        or _looks_percent_formula_leaf(param.key, param.path)
        or any(token in _field_leaf_token(param.key, param.path) for token in ("date", "time", "duration", "day"))
    ):
        return True
    if kind in {"percent_of", "remainder_after_percent"} and (
        _looks_total_formula_leaf(param.key, param.path)
        or "price" in _field_leaf_token(param.key, param.path)
        or "amount" in _field_leaf_token(param.key, param.path)
    ):
        return True
    if kind in {"sum", "difference"} and _looks_total_formula_leaf(param.key, param.path):
        return True
    return False


def _arithmetic_strong_structure(
    target: ParamField,
    left: ParamField,
    right: ParamField,
    kind: str,
) -> bool:
    """Single-sample formulas need a readonly/derived target and typed operands."""
    # These strategies calculate amounts/totals from rates; none calculates a
    # rate itself. A coincidental numeric equality must not turn an editable
    # percentage field into a difference/sum result.
    if _looks_percent_formula_leaf(target.key, target.path):
        return False
    if _param_has_editable_control_evidence(target) and not _param_control_is_readonly(target):
        return False
    target_leaf = _field_leaf_token(target.key, target.path)
    derived = (
        _looks_total_formula_leaf(target.key, target.path)
        or _looks_percent_formula_leaf(target.key, target.path)
        or any(token in target_leaf for token in ("duration", "payable", "subtotal"))
    )
    if not derived:
        return False
    if not (
        _arithmetic_operand_semantic_ok(left, kind=kind)
        and _arithmetic_operand_semantic_ok(right, kind=kind)
    ):
        return False
    prefix = _param_group_prefix(target.path)
    if _param_group_prefix(left.path) != prefix or _param_group_prefix(right.path) != prefix:
        return False
    if kind in {"sum", "difference"} and not (
        _looks_percent_formula_leaf(left.key, left.path)
        or _looks_percent_formula_leaf(right.key, right.path)
        or _looks_total_formula_leaf(left.key, left.path)
        or _looks_total_formula_leaf(right.key, right.path)
    ):
        return False
    return True


def _infer_arithmetic_computed_fields(spec: FlowSpec) -> None:
    """Hide numeric fields that the recorded values prove are derived from siblings."""
    changed = True
    while changed:
        changed = False
        for step in spec.steps or []:
            numeric = [
                (param, number)
                for param in step.params or []
                if (number := _as_finite_number(param.value)) is not None
            ]
            ranked: list[tuple[int, int, int, ParamField, str, ParamField, ParamField]] = []
            for target, target_number in numeric:
                if not _arithmetic_target_allowed(target):
                    continue
                prefix = _param_group_prefix(target.path)
                local = [
                    (param, number)
                    for param, number in numeric
                    if param is not target and _param_group_prefix(param.path) == prefix
                ]
                picked = _pick_arithmetic_match(target, target_number, local)
                if picked is None:
                    global_siblings = [
                        (param, number) for param, number in numeric if param is not target
                    ]
                    if any(
                        _looks_percent_formula_leaf(param.key, param.path)
                        for param, _number in global_siblings
                    ) and (
                        _looks_total_formula_leaf(target.key, target.path)
                        or _looks_percent_formula_leaf(target.key, target.path)
                    ):
                        picked = _pick_arithmetic_match(target, target_number, global_siblings)
                if picked is None:
                    continue
                kind, left, right = picked
                if not _arithmetic_strong_structure(target, left, right, kind):
                    continue
                ranked.append((
                    int(kind in {"percent_of", "remainder_after_percent"}),
                    int(
                        _looks_percent_formula_leaf(left.key, left.path)
                        or _looks_percent_formula_leaf(right.key, right.path)
                    ),
                    int(_looks_total_formula_leaf(target.key, target.path)),
                    target, kind, left, right,
                ))
            if not ranked:
                continue
            ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
            target, kind, left, right = ranked[0][3:]
            target.category = "runtime_var"
            target.source_kind = "computed"
            target.source = {
                "kind": "computed",
                "strategy": kind,
                "left_field": left.key,
                "right_field": right.key,
                "result_field": target.key,
                "path": target.path,
                "sample_verified": True,
            }
            target.exposed_to_user = False
            target.editable = False
            target.required = False
            target.need_human_confirm = False
            if (
                not _param_field_manually_edited(target, "type")
                and (
                    str(target.wire_type or "") == "number"
                    or _as_finite_number(target.value) is not None
                )
            ):
                target.type = "number"
            target.reason = (
                f"录制样例证明该字段由 `{left.key}` 与 `{right.key}` 按 {kind} 计算得到，运行期自动计算"
            )
            step.sample_inputs.pop(target.key, None)
            changed = True


def _infer_collection_computed_fields(spec: FlowSpec) -> None:
    """Infer totals over dynamic rows without hard-coding a page or endpoint."""
    for step in spec.steps or []:
        aggregates = [
            param for param in step.params or []
            if str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
            and str((param.source or {}).get("structure_kind") or "") == "array_object"
        ]
        for aggregate in aggregates:
            container = str((aggregate.source or {}).get("array_container_path") or aggregate.path or "")
            container = container.removeprefix("body.")
            if not container:
                continue
            rows_by_leaf: dict[str, list[tuple[ParamField, float]]] = {}
            pattern = re.compile(rf"^{re.escape(container)}\[\d+\]\.(.+)$")
            for param in step.params or []:
                match = pattern.match(str(param.path or "").removeprefix("body."))
                number = _as_finite_number(param.value)
                if match and number is not None:
                    rows_by_leaf.setdefault(match.group(1), []).append((param, number))
            base_candidates = [
                (
                    leaf,
                    sum(number for _param, number in values),
                    max(
                        int(str((param.source or {}).get("strategy") or "") in {"sum", "difference"}) * 4
                        + int(_field_leaf_token(param.key, param.path) in {"totalprice", "linetotal", "linetotalprice"}) * 2
                        + int(_looks_total_formula_leaf(param.key, param.path))
                        for param, _number in values
                    ),
                )
                for leaf, values in rows_by_leaf.items()
                if values and all(
                    _looks_total_formula_leaf(param.key, param.path)
                    or param.source_kind == "computed"
                    for param, _number in values
                )
            ]
            if base_candidates:
                best_rank = max(item[2] for item in base_candidates)
                strongest = [item for item in base_candidates if item[2] == best_rank]
            else:
                strongest = []
            if len(strongest) == 1:
                item_field, collection_sum, _rank = strongest[0]
            else:
                # Sample arithmetic is ambiguous, but the independent
                # root↔row structural pass below can still prove a unique sum.
                item_field, collection_sum = "", float("nan")
            root_params = [
                param for param in step.params or []
                if "[" not in str(param.path or "")
                and param is not aggregate
                and _as_finite_number(param.value) is not None
            ]
            assigned: set[str] = set()

            def assign(
                target: ParamField,
                strategy: str,
                *,
                sample_verified: bool = True,
                **source: Any,
            ) -> None:
                target.category = "runtime_var"
                target.source_kind = "computed"
                target.source = {
                    "kind": "computed",
                    "strategy": strategy,
                    "container_field": str(aggregate.key or aggregate.path),
                    "item_field": item_field,
                    "result_field": target.key,
                    "path": target.path,
                    "sample_verified": sample_verified,
                    **({"structural_verified": True} if not sample_verified else {}),
                    **source,
                }
                target.type = "number"
                target.exposed_to_user = False
                target.editable = False
                target.required = False
                target.need_human_confirm = False
                target.reason = (
                    "录制样例和动态明细结构共同证明该字段由明细集合运行期汇总计算"
                    if sample_verified
                    else "只读汇总字段与重复明细字段具有唯一结构对应，运行期按全部明细汇总计算"
                )
                step.sample_inputs.pop(target.key, None)
                assigned.add(target.key)

            percent_params = [
                param for param in root_params
                if _looks_percent_formula_leaf(param.key, param.path)
            ]
            for target in root_params:
                if not _arithmetic_target_allowed(target):
                    continue
                target_number = float(_as_finite_number(target.value) or 0.0)
                percent_matches = [
                    percent for percent in percent_params
                    if percent is not target
                    and _numbers_match(
                        target_number,
                        collection_sum * float(_as_finite_number(percent.value) or 0.0) / 100.0,
                    )
                ]
                if len(percent_matches) == 1 and (
                    "discount" in _field_leaf_token(target.key, target.path)
                    or "tax" in _field_leaf_token(target.key, target.path)
                ):
                    assign(
                        target,
                        "percent_of_collection_sum",
                        right_field=percent_matches[0].key,
                    )
            for target in root_params:
                if target.key in assigned or not _arithmetic_target_allowed(target):
                    continue
                target_number = float(_as_finite_number(target.value) or 0.0)
                derived_matches = [
                    other for other in root_params
                    if other is not target
                    and other.source_kind == "computed"
                    and _numbers_match(
                        target_number,
                        collection_sum - float(_as_finite_number(other.value) or 0.0),
                    )
                ]
                if len(derived_matches) == 1 and _looks_total_formula_leaf(target.key, target.path):
                    assign(
                        target,
                        "difference_collection_sum",
                        right_field=derived_matches[0].key,
                    )
                elif _numbers_match(target_number, collection_sum) and _looks_total_formula_leaf(target.key, target.path):
                    assign(target, "collection_sum")

            item_params_by_leaf: dict[str, list[ParamField]] = {}
            for param in step.params or []:
                match = pattern.match(str(param.path or "").removeprefix("body."))
                if match:
                    item_params_by_leaf.setdefault(match.group(1), []).append(param)
            for target in root_params:
                if (
                    target.key in assigned
                    or target.source_kind == "computed"
                    or not _arithmetic_target_allowed(target)
                    or not _looks_total_formula_leaf(target.key, target.path)
                ):
                    continue
                target_leaf = _field_leaf_token(target.key, target.path)
                ranked_matches: list[tuple[int, str]] = []
                for item_path, item_params in item_params_by_leaf.items():
                    item_leaf = _field_leaf_token(
                        item_params[0].key if item_params else item_path,
                        item_path,
                    )
                    rank = (
                        2 if target_leaf == item_leaf
                        else 1 if target_leaf == f"total{item_leaf}" else 0
                    )
                    if rank:
                        ranked_matches.append((rank, item_path))
                if not ranked_matches:
                    continue
                best_rank = max(rank for rank, _item_path in ranked_matches)
                strongest_paths = {
                    item_path for rank, item_path in ranked_matches if rank == best_rank
                }
                if len(strongest_paths) != 1:
                    continue
                assign(
                    target,
                    "collection_sum",
                    sample_verified=False,
                    item_field=next(iter(strongest_paths)),
                )


def _param_is_temporal(param: ParamField) -> bool:
    if str(param.type or param.wire_type or "").lower() in {"date", "datetime", "time"}:
        return True
    if any(
        isinstance(item, dict)
        and str(item.get("control_kind") or "").lower() in {"date", "datetime", "time"}
        for item in (param.evidence or [])
    ):
        return True
    text = str(param.value or "").strip()
    return bool(
        re.fullmatch(r"-?\d{10}|-?\d{13}", text)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ tT]\d{2}:\d{2}(?::\d{2})?)?", text)
    )


def _date_span_target_allowed(param: ParamField) -> bool:
    """Return whether one field has its own structural duration evidence."""
    if (
        _looks_pagination_field(param.key, param.path)
        or _looks_non_quantity_formula_leaf(param.key, param.path)
        or _param_is_document_record_identity(param)
        or _param_has_editable_control_evidence(param)
        or param.source_kind in _OPTION_SOURCE_KINDS
        or param.type in {"enum", "list-enum"}
        or _param_control_kinds(param) & {"select", "combobox", "radio"}
    ):
        return False
    raw = param.key or str(param.path or "").split(".")[-1]
    normalized = re.sub(r"[^a-z0-9]+", "", str(raw).lower())
    named_duration = bool(
        re.fullmatch(r"(?:day|days|duration|durationdays)", normalized)
    )
    readonly_derived = any(
        isinstance(item, dict)
        and item.get("kind") == "page_control"
        and (item.get("read_only") or item.get("disabled"))
        for item in (param.evidence or [])
    )
    return named_duration or readonly_derived


def _repair_invalid_date_span_contracts(spec: FlowSpec) -> int:
    """Invalidate legacy date-span guesses that fail today's evidence gate."""
    repaired = 0
    for step in spec.steps or []:
        for param in step.params or []:
            source = dict(param.source or {})
            if (
                param.source_kind != "computed"
                or str(source.get("strategy") or "")
                not in {"date_span_days", "date_span_days_json"}
                or _date_span_target_allowed(param)
            ):
                continue
            param.category = "runtime_var"
            param.source_kind = "unknown"
            param.source = {
                "kind": "unresolved",
                "path": param.path,
                "invalidated_contract": "unsupported_date_span_inference",
            }
            param.required = False
            param.exposed_to_user = False
            param.editable = False
            param.need_human_confirm = True
            param.reason = (
                "旧日期跨度结论缺少目标字段自身的时长/只读派生结构证据，已撤销并重新按请求链判定来源"
            )
            repaired += 1
    return repaired


def _infer_computed_runtime_fields(spec: FlowSpec) -> None:
    """Hide recorded computed fields only when their samples prove the formula."""
    for step in spec.steps or []:
        for param in step.params or []:
            strategy = str((param.source or {}).get("strategy") or "")
            if (
                param.source_kind != "computed"
                or strategy not in {item[0] for item in _ARITHMETIC_STRATEGIES}
                or not _looks_percent_formula_leaf(param.key, param.path)
                or param.locked
                or _param_source_agent_classified(param)
                or any(
                    _param_field_manually_edited(param, field)
                    for field in ("source", "source_kind", "category")
                )
            ):
                continue
            param.category = "user_param"
            param.source_kind = "user_input"
            param.source = {
                "kind": "sample",
                "path": param.path,
                "reason": "unsupported_percentage_result_formula_removed",
            }
            param.exposed_to_user = True
            param.editable = True
            param.need_human_confirm = False
            param.reason = "该百分比字段没有可执行的百分比结果公式，按调用方可提供字段处理"
    _apply_date_range_companions(spec)
    _infer_arithmetic_computed_fields(spec)
    _infer_collection_computed_fields(spec)
    def leaf_name(param: ParamField) -> str:
        raw = param.key or str(param.path or "").split(".")[-1]
        return re.sub(r"[^a-z0-9]+", "", str(raw).lower())

    date_pairs: list[tuple[FlowStep, ParamField, ParamField, int]] = []
    for step in spec.steps or []:
        temporals = [
            param for param in step.params or []
            if _param_is_temporal(param) and _date_like_epoch_seconds(param.value) is not None
        ]
        for index, left in enumerate(temporals):
            for right in temporals[index + 1:]:
                left_seconds = _date_like_epoch_seconds(left.value)
                right_seconds = _date_like_epoch_seconds(right.value)
                if left_seconds is None or right_seconds is None:
                    continue
                start, end = (left, right) if left_seconds <= right_seconds else (right, left)
                date_pairs.append((
                    step, start, end,
                    int(round(abs(right_seconds - left_seconds) / 86400.0)),
                ))
    if not date_pairs:
        return

    capability_memberships = [
        set(_capability_node_step_ids(capability))
        for capability in spec.capabilities or []
    ]

    def pair_rank(step: FlowStep, pair: tuple[FlowStep, ParamField, ParamField, int]) -> tuple[int, int, float]:
        source_step = pair[0]
        same_step = int(source_step.step_id == step.step_id)
        shared_capability = int(any(
            step.step_id in members and source_step.step_id in members
            for members in capability_memberships
        ))
        target_sequence = _step_sequence(step)
        source_sequence = _step_sequence(source_step)
        distance = (
            abs(target_sequence - source_sequence)
            if target_sequence is not None and source_sequence is not None
            else 10**9
        )
        return same_step, shared_capability, -distance

    assignments: list[tuple[FlowStep, ParamField, dict[str, Any], str]] = []
    for step in spec.steps or []:
        for param in step.params or []:
            if (
                param.locked
                or _param_source_agent_classified(param)
                or _param_has_editable_control_evidence(param)
                or _param_is_temporal(param)
                or _looks_pagination_field(param.key, param.path)
                or _looks_non_quantity_formula_leaf(param.key, param.path)
                or param.source_kind in {
                    "computed", "selected_option_field", "api_option",
                    "form_option", "page_enum", "current_user",
                }
            ):
                continue
            key_norm = leaf_name(param)
            strategy = ""
            output_key = ""
            sample_value: Any = None
            if (
                str(param.path or "").startswith("query.")
                and re.search(r"(process)?variables?(str)?$|context(json|str)?$", key_norm)
            ):
                try:
                    payload = json.loads(str(param.value or ""))
                except Exception:  # noqa: BLE001
                    payload = None
                if isinstance(payload, dict) and len(payload) == 1:
                    output_key, sample_value = next(iter(payload.items()))
                    strategy = "date_span_days_json"
            elif _as_finite_number(param.value) is not None:
                sample_value = param.value
                strategy = "date_span_days"
            if not strategy:
                continue
            try:
                observed_days = int(sample_value)
            except (TypeError, ValueError):
                continue
            if observed_days < 0 or observed_days > 3660:
                continue
            named = bool(
                strategy == "date_span_days_json"
                or re.fullmatch(r"(?:day|days|duration|durationdays)", key_norm)
            )
            readonly_calc = any(
                isinstance(item, dict)
                and item.get("kind") == "page_control"
                and (item.get("read_only") or item.get("disabled"))
                for item in (param.evidence or [])
            )
            # A matching numeric sample next to two dates is only a
            # coincidence unless the target itself carries duration semantics
            # or the page proves that it is a derived, non-editable output.
            # Pagination, identities, codes and audit/state leaves were
            # rejected above so they cannot be hidden as date-span formulas.
            if not _date_span_target_allowed(param) or not (named or readonly_calc):
                continue
            matches = [pair for pair in date_pairs if pair[3] == observed_days]
            if not matches:
                continue
            ranked = sorted(matches, key=lambda pair: pair_rank(step, pair), reverse=True)
            if len(ranked) > 1 and pair_rank(step, ranked[0]) == pair_rank(step, ranked[1]):
                continue
            _source_step, start, end, _days = ranked[0]
            assignments.append((step, param, {
                "kind": "computed",
                "strategy": strategy,
                "start_field": start.key,
                "end_field": end.key,
                "path": param.path,
                "sample_verified": True,
                "sample_days": observed_days,
                **({"output_key": str(output_key)} if output_key else {}),
            }, f"录制样例证明该字段由 `{start.key}` 与 `{end.key}` 的日期跨度生成，运行期自动计算"))

    claimed_pairs: dict[tuple[str, str, str], int] = {}
    for step, param, source, _reason in assignments:
        key = (step.step_id, str(source.get("start_field")), str(source.get("end_field")))
        claimed_pairs[key] = claimed_pairs.get(key, 0) + 1
    for step, param, source, reason in assignments:
        key = (step.step_id, str(source.get("start_field")), str(source.get("end_field")))
        if claimed_pairs.get(key, 0) != 1:
            continue
        param.category = "runtime_var"
        param.source_kind = "computed"
        param.source = source
        param.exposed_to_user = False
        param.editable = False
        param.required = False
        param.need_human_confirm = False
        param.reason = reason
        step.sample_inputs.pop(param.key, None)


_COMPUTED_DATE_STRATEGIES = frozenset({"date_span_days", "date_span_days_json"})


_COMPUTED_ARITHMETIC_STRATEGIES = frozenset({
    "product", "sum", "percent_of", "remainder_after_percent", "difference",
})

_COMPUTED_COLLECTION_STRATEGIES = frozenset({
    "collection_sum", "percent_of_collection_sum", "difference_collection_sum",
})


def _computed_formula_is_complete(source: dict | None) -> bool:
    source = source or {}
    strategy = str(source.get("strategy") or "")
    if strategy in _COMPUTED_DATE_STRATEGIES:
        return bool(source.get("start_field") and source.get("end_field"))
    if strategy in _COMPUTED_ARITHMETIC_STRATEGIES:
        return bool(source.get("left_field") and source.get("right_field"))
    if strategy in _COMPUTED_COLLECTION_STRATEGIES:
        return bool(
            source.get("container_field")
            and source.get("item_field")
            and (
                strategy == "collection_sum"
                or source.get("right_field")
            )
        )
    return False

_PENDING_FLOW_SPEC_HELPERS = {'_apply_date_range_companions': 'dano.execution.page.flow_materialization.field_contracts.page_rules', '_capability_node_step_ids': 'dano.execution.page.capability_refs'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
