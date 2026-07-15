"""Executable enum contracts with static coverage and runtime resolution.

Coverage/query models live in :mod:`dano_recording.domain.enums`; this module
owns only execution.  It supports exact lookup, search, numbered/cursor
pagination, unique label matching and direct wire-value validation.
"""

from __future__ import annotations

import inspect
import json
import re
from enum import StrEnum
from typing import Any, Awaitable, Callable

from pydantic import Field, model_validator

from dano_recording.domain._base import FrozenModel, freeze_json
from dano_recording.domain.enums import (
    ChoiceOption,
    EnumEvidence,
    EnumSourceQuery,
    MappingCoverage,
    PaginationContract,
    SnapshotCoverage,
    SnapshotCoverageKind,
    SourceScope,
)


class EnumInputMode(StrEnum):
    LABEL_OR_VALUE = "label_or_value"
    LABEL = "label"
    WIRE_VALUE = "wire_value"


class EnumRuntimeContract(FrozenModel):
    evidence: EnumEvidence
    options: tuple[ChoiceOption, ...] = ()
    input_mode: EnumInputMode = EnumInputMode.LABEL_OR_VALUE

    @model_validator(mode="after")
    def _static_domain_has_values(self) -> "EnumRuntimeContract":
        if self.evidence.mapping_coverage is MappingCoverage.STATIC_DOMAIN and not self.options:
            raise ValueError("static_domain enum requires static options")
        return self


class EnumContractFault(FrozenModel):
    code: str = "enum_wire_mapping_unavailable"
    message: str = "当前枚举不能将调用方输入稳定转换为接口 wire value。"


class EnumResolution(FrozenModel):
    wire_value: Any
    label: str | None = None
    matched_by: str
    request_definition_id: str | None = None
    pages_fetched: int = Field(default=0, ge=0)
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _freeze_value(self) -> "EnumResolution":
        object.__setattr__(self, "wire_value", freeze_json(self.wire_value))
        return self


class EnumResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _as_contract(
    value: EnumRuntimeContract | EnumEvidence,
    *,
    options: tuple[ChoiceOption, ...] = (),
    input_mode: EnumInputMode | None = None,
) -> EnumRuntimeContract:
    if isinstance(value, EnumRuntimeContract):
        if options:
            raise ValueError("options cannot be supplied twice")
        if input_mode is not None and input_mode is not value.input_mode:
            return value.model_copy(update={"input_mode": input_mode}, deep=True)
        return value
    return EnumRuntimeContract(
        evidence=value,
        options=options,
        input_mode=input_mode or EnumInputMode.LABEL_OR_VALUE,
    )


def enum_contract_fault(
    value: EnumRuntimeContract | EnumEvidence,
    *,
    options: tuple[ChoiceOption, ...] = (),
    input_mode: EnumInputMode | None = None,
) -> EnumContractFault | None:
    contract = _as_contract(value, options=options, input_mode=input_mode)
    if contract.input_mode is EnumInputMode.WIRE_VALUE:
        return None
    if (
        contract.evidence.mapping_coverage is MappingCoverage.STATIC_DOMAIN
        and contract.options
    ):
        return None
    if (
        contract.evidence.mapping_coverage is MappingCoverage.RUNTIME_RESOLVABLE
        and contract.evidence.source_query is not None
    ):
        return None
    return EnumContractFault()


_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _lookup(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for match in _PATH_TOKEN.finditer(path):
        key, index = match.groups()
        if index is not None:
            if not isinstance(current, (list, tuple)):
                raise KeyError(path)
            current = current[int(index)]
        else:
            if not isinstance(current, dict) or key not in current:
                raise KeyError(path)
            current = current[key]
    return current


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    tokens = [match.group(1) for match in _PATH_TOKEN.finditer(path) if match.group(1)]
    if not tokens:
        raise ValueError(f"invalid request-template path: {path}")
    current = target
    for token in tokens[:-1]:
        child = current.get(token)
        if not isinstance(child, dict):
            child = {}
            current[token] = child
        current = child
    current[tokens[-1]] = value


def _thaw(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _replace_placeholders(value: Any, *, label: Any, wire_value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _replace_placeholders(item, label=label, wire_value=wire_value)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_placeholders(item, label=label, wire_value=wire_value)
            for item in value
        ]
    if value == "{{label}}":
        return label
    if value == "{{wire_value}}":
        return wire_value
    if isinstance(value, str):
        return value.replace("{{label}}", str(label)).replace(
            "{{wire_value}}", str(wire_value)
        )
    return value


def _stable(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _label_equal(left: Any, right: Any) -> bool:
    return str(left).strip().casefold() == str(right).strip().casefold()


def _extract_options(payload: Any, query: EnumSourceQuery) -> tuple[ChoiceOption, ...]:
    records_path = query.pagination.records_path if query.pagination else None
    try:
        rows = _lookup(payload, records_path)
    except (KeyError, IndexError, TypeError):
        return ()
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("data") or rows.get("results") or []
    if not isinstance(rows, (list, tuple)):
        return ()
    output: list[ChoiceOption] = []
    seen: set[tuple[str, str]] = set()
    for row in rows[:10_000]:
        try:
            label = _lookup(row, query.label_path)
            wire_value = _lookup(row, query.value_path)
        except (KeyError, IndexError, TypeError):
            continue
        key = (str(label), _stable(wire_value))
        if key in seen:
            continue
        seen.add(key)
        output.append(ChoiceOption(label=str(label), value=wire_value))
    return tuple(output)


Fetcher = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class EnumRuntimeResolver:
    def __init__(self, fetcher: Fetcher | None = None) -> None:
        self.fetcher = fetcher

    @staticmethod
    def _matches(
        options: tuple[ChoiceOption, ...],
        supplied: Any,
        input_mode: EnumInputMode,
    ) -> tuple[ChoiceOption, ...]:
        output = []
        for option in options:
            value_match = supplied == option.value or str(supplied) == str(option.value)
            label_match = _label_equal(supplied, option.label)
            if input_mode is EnumInputMode.LABEL and label_match:
                output.append(option)
            elif input_mode is EnumInputMode.WIRE_VALUE and value_match:
                output.append(option)
            elif input_mode is EnumInputMode.LABEL_OR_VALUE and (value_match or label_match):
                output.append(option)
        return tuple(output)

    async def resolve(
        self,
        value: EnumRuntimeContract | EnumEvidence,
        supplied: Any,
        *,
        options: tuple[ChoiceOption, ...] = (),
        input_mode: EnumInputMode | None = None,
    ) -> EnumResolution:
        contract = _as_contract(value, options=options, input_mode=input_mode)
        evidence = contract.evidence
        static_matches = self._matches(contract.options, supplied, contract.input_mode)
        unique_values = {_stable(option.value) for option in static_matches}
        if len(unique_values) == 1 and static_matches:
            option = static_matches[0]
            return EnumResolution(
                wire_value=option.value,
                label=option.label,
                matched_by="static_value"
                if supplied == option.value or str(supplied) == str(option.value)
                else "static_label",
                evidence_ids=evidence.evidence_ids,
            )
        if len(unique_values) > 1:
            raise EnumResolutionError(
                "ambiguous_enum_label",
                f"enum label {supplied!r} maps to multiple wire values",
            )

        query = evidence.source_query
        if query is not None and self.fetcher is not None:
            return await self._resolve_dynamic(contract, query, supplied)
        if contract.input_mode is EnumInputMode.WIRE_VALUE:
            if evidence.mapping_coverage is MappingCoverage.STATIC_DOMAIN and contract.options:
                raise EnumResolutionError(
                    "enum_wire_value_not_allowed",
                    f"wire value {supplied!r} is outside the static enum domain",
                )
            return EnumResolution(
                wire_value=supplied,
                matched_by="caller_wire_value",
                evidence_ids=evidence.evidence_ids,
            )
        fault = enum_contract_fault(contract)
        if fault is not None:
            raise EnumResolutionError(fault.code, fault.message)
        raise EnumResolutionError("enum_value_not_found", f"enum value {supplied!r} was not found")

    async def _fetch(self, request: dict[str, Any]) -> Any:
        if self.fetcher is None:
            raise EnumResolutionError("enum_fetcher_unavailable", "enum runtime fetcher is unavailable")
        result = self.fetcher(request)
        return await result if inspect.isawaitable(result) else result

    async def _resolve_dynamic(
        self,
        contract: EnumRuntimeContract,
        query: EnumSourceQuery,
        supplied: Any,
    ) -> EnumResolution:
        wants_wire = contract.input_mode is EnumInputMode.WIRE_VALUE
        template = _replace_placeholders(
            _thaw(query.request_template),
            label=None if wants_wire else supplied,
            wire_value=supplied if wants_wire else None,
        )
        if not isinstance(template, dict):
            raise EnumResolutionError(
                "enum_request_template_invalid",
                "enum request template must be an object",
            )
        template.setdefault("method", query.method)
        template.setdefault("request_definition_id", query.request_definition_id)
        if not wants_wire and query.search_param:
            _set_path(template, query.search_param, supplied)

        pagination = query.pagination
        options: list[ChoiceOption] = []
        pages_fetched = 0
        cursor: Any = None
        page = pagination.start_page if pagination else 1
        scan_complete = pagination is None
        exact_result = False
        for _ in range(100 if pagination else 1):
            request = _thaw(template)
            if pagination is not None:
                if pagination.page_param:
                    _set_path(request, pagination.page_param, page)
                    if pagination.page_size_param and pagination.page_size:
                        _set_path(
                            request,
                            pagination.page_size_param,
                            pagination.page_size,
                        )
                elif pagination.cursor_param and cursor is not None:
                    _set_path(request, pagination.cursor_param, cursor)
            payload = await self._fetch(request)
            pages_fetched += 1
            page_options = _extract_options(payload, query)
            options.extend(page_options)

            matches = self._matches(tuple(options), supplied, contract.input_mode)
            unique_values = {_stable(option.value) for option in matches}
            # ``exact_lookup`` is an explicit server contract that one exact
            # query uniquely identifies the domain entry.
            if query.exact_lookup and len(unique_values) == 1:
                exact_result = True
                break
            if pagination is None:
                scan_complete = True
                break
            if pagination.page_param:
                if not page_options:
                    scan_complete = True
                    break
                if pagination.page_size and len(page_options) < pagination.page_size:
                    scan_complete = True
                    break
                page += 1
                continue
            if pagination.cursor_param and pagination.next_cursor_path:
                try:
                    next_cursor = _lookup(payload, pagination.next_cursor_path)
                except (KeyError, IndexError, TypeError):
                    scan_complete = True
                    break
                if next_cursor in {None, ""} or next_cursor == cursor:
                    scan_complete = True
                    break
                cursor = next_cursor
                continue
            scan_complete = True
            break

        if pagination is not None and not exact_result and not scan_complete:
            raise EnumResolutionError(
                "enum_pagination_incomplete",
                "enum pagination exceeded its bounded scan before uniqueness was proven",
            )

        matches = self._matches(tuple(options), supplied, contract.input_mode)
        unique_values = {_stable(option.value) for option in matches}
        if len(unique_values) > 1:
            raise EnumResolutionError(
                "ambiguous_enum_label",
                f"enum label {supplied!r} maps to multiple wire values",
            )
        if not matches:
            raise EnumResolutionError(
                "enum_value_not_found",
                f"enum value {supplied!r} was not found in runtime source",
            )
        option = matches[0]
        return EnumResolution(
            wire_value=option.value,
            label=option.label,
            matched_by="runtime_wire_value" if wants_wire else "runtime_label",
            request_definition_id=query.request_definition_id,
            pages_fetched=pages_fetched,
            evidence_ids=contract.evidence.evidence_ids,
        )


__all__ = [
    "EnumContractFault",
    "EnumEvidence",
    "EnumInputMode",
    "EnumResolution",
    "EnumResolutionError",
    "EnumRuntimeContract",
    "EnumRuntimeResolver",
    "EnumSourceQuery",
    "MappingCoverage",
    "PaginationContract",
    "SnapshotCoverage",
    "SnapshotCoverageKind",
    "SourceScope",
    "enum_contract_fault",
]
