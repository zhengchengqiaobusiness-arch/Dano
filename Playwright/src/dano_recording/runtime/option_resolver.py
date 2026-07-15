"""Resolve static or API-backed choice contracts without polluting fields."""

from __future__ import annotations

from typing import Any

from dano_recording.domain.enums import ChoiceOption, EnumEvidence
from dano_recording.enum_resolver import (
    EnumInputMode,
    EnumResolution,
    EnumRuntimeContract,
    EnumRuntimeResolver,
    Fetcher,
)


def _matches(item: Any, option: dict[str, Any]) -> bool:
    candidates = (option.get("value"), option.get("label"))
    return any(item == candidate or str(item) == str(candidate) for candidate in candidates)


def option_value(contract: dict, supplied: Any) -> Any:
    options = contract.get("typed_options") or contract.get("options") or []
    multiple = bool(contract.get("multiple"))
    values = supplied if multiple and isinstance(supplied, list) else [supplied]
    resolved: list[Any] = []
    for item in values:
        match = next(
            (opt for opt in options if isinstance(opt, dict) and _matches(item, opt)),
            None,
        )
        if match is None and options and not contract.get("allow_custom", False):
            raise ValueError(
                f"unknown option for {contract.get('public_name') or contract.get('field')}: {item}"
            )
        resolved.append(match.get("value") if match else item)
    return resolved if multiple else resolved[0]


def resolve_choices(fields: dict[str, Any], contracts: list[dict]) -> dict[str, Any]:
    result = dict(fields)
    for contract in contracts:
        name = str(contract.get("public_name") or contract.get("field") or "")
        if name and name in result:
            result[name] = option_value(contract, result[name])
    return result


def enum_runtime_contract(contract: dict[str, Any]) -> EnumRuntimeContract | None:
    """Parse a V3 evidence-backed enum contract.

    Legacy ``choice_contracts`` intentionally return ``None`` and continue
    through their compatibility resolver.  A modern contract must carry its
    coverage/evidence declaration so an observed page is never mistaken for a
    complete static domain.
    """

    raw_evidence = contract.get("enum_evidence")
    if not isinstance(raw_evidence, dict):
        if not any(key in contract for key in (
            "mapping_coverage", "source_query", "snapshot_coverage",
        )):
            return None
        raw_evidence = {
            key: contract[key] for key in (
                "selected_pair_verified", "observed_mapping_complete",
                "snapshot_coverage", "mapping_coverage", "source_scope",
                "source_query", "evidence_ids",
            ) if key in contract
        }
    evidence = EnumEvidence.model_validate(raw_evidence)
    options = tuple(
        ChoiceOption.model_validate(item)
        for item in contract.get("typed_options") or contract.get("options") or []
        if isinstance(item, dict) and "label" in item and "value" in item
    )
    return EnumRuntimeContract(
        evidence=evidence,
        options=options,
        input_mode=EnumInputMode(str(contract.get("input_mode") or "label_or_value")),
    )


async def resolve_evidence_choice(
    contract: dict[str, Any],
    supplied: Any,
    *,
    fetcher: Fetcher | None,
) -> tuple[Any, tuple[EnumResolution, ...]]:
    parsed = enum_runtime_contract(contract)
    if parsed is None:
        raise ValueError("choice contract is not evidence-backed")
    multiple = bool(contract.get("multiple"))
    values = supplied if multiple and isinstance(supplied, list) else [supplied]
    resolver = EnumRuntimeResolver(fetcher)
    resolutions = tuple([await resolver.resolve(parsed, value) for value in values])
    wire_values = [item.wire_value for item in resolutions]
    return (wire_values if multiple else wire_values[0]), resolutions


def extract_dynamic_options(payload: Any, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract typed options from a trusted option-source response declaratively."""

    from .request_builder import lookup

    path = str(contract.get("options_path") or contract.get("source_path") or "")
    if path:
        try:
            payload = lookup({"response": payload}, path if path.startswith("response") else f"response.{path}")
        except KeyError:
            return []
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("data") or payload.get("results") or []
    if not isinstance(payload, list):
        return []
    value_path = str(contract.get("value_path") or "value")
    label_path = str(contract.get("label_path") or "label")
    options: list[dict[str, Any]] = []
    for item in payload[:10_000]:
        if isinstance(item, dict):
            try:
                value = lookup(item, value_path)
            except KeyError:
                value = item.get("id", item.get("key"))
            try:
                label = lookup(item, label_path)
            except KeyError:
                label = item.get("name", item.get("title", value))
        else:
            value = label = item
        if value is not None:
            options.append({"value": value, "label": str(label if label is not None else value)})
    return options
