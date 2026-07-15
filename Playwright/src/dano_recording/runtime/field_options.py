"""List one V3 choice field through the verified runtime request boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from dano_recording.enum_resolver import enum_contract_fault

from .capability_executor import _resolve_ref_step, _select, _step_indexes
from .option_resolver import enum_runtime_contract, extract_dynamic_options
from .request_builder import CredentialHeaders
from .workflow_executor import (
    AddressResolver,
    AsyncSender,
    execute_recording_workflow,
)


def _result(
    field: str,
    *,
    options: list[dict[str, Any]] | None = None,
    note: str,
    capability: str,
    **extra: Any,
) -> dict[str, Any]:
    values = list(options or [])[:10_000]
    return {
        "field": field,
        "options": values,
        "count": len(values),
        "note": note,
        "capability": capability,
        **extra,
    }


def _normalized_options(contract: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in contract.get("typed_options") or contract.get("options") or []:
        if not isinstance(raw, dict) or raw.get("value") is None:
            continue
        label = str(raw.get("label", raw["value"]))
        key = (label, repr(raw["value"]))
        if key in seen:
            continue
        seen.add(key)
        output.append({"label": label, "value": raw["value"]})
    return output


async def list_recording_field_options(
    api_request: dict[str, Any],
    field: str,
    *,
    capability: str | None,
    base_url: str,
    credential_headers: CredentialHeaders | None = None,
    runtime_context: dict[str, Any] | None = None,
    sender: AsyncSender | None = None,
    allow_private_networks: bool = False,
    address_resolver: AddressResolver | None = None,
) -> dict[str, Any]:
    """Return current typed options without entering the legacy request chain.

    Dynamic sources are restricted to the exact captured option-source request,
    GET/HEAD, the published origin, trusted credentials and the normal V3 SSRF
    policy.  An unverified publication is rejected before any transport call.
    """

    requested_capability = str(capability or "")
    if api_request.get("recording_engine") != "playwright_v3":
        raise ValueError("V3 option listing requires recording_engine=playwright_v3")
    if not (
        str(api_request.get("verification_status") or "") == "verified"
        and api_request.get("direct_call_enabled") is True
    ):
        return _result(
            field,
            note="V3 录制资产未验证，禁止读取动态选项",
            capability=requested_capability,
            ok=False,
            blocked=True,
            stage="unverified_contract",
        )
    try:
        cap = _select(api_request, capability)
    except ValueError as exc:
        return _result(
            field,
            note=str(exc),
            capability=requested_capability,
            ok=False,
            blocked=True,
            stage="capability_not_found",
        )
    capability_name = requested_capability or str(
        cap.get("capability_uuid") or cap.get("name") or cap.get("kind") or ""
    )
    contracts = [
        dict(item)
        for item in cap.get("choice_contracts") or []
        if isinstance(item, dict)
        and field in {
            str(item.get("public_name") or ""),
            str(item.get("field") or ""),
            str(item.get("field_uuid") or ""),
            str(item.get("field_contract_id") or ""),
        }
    ]
    if len(contracts) != 1:
        return _result(
            field,
            note="该字段没有唯一的 V3 选择契约",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="field_contract_not_found",
        )
    contract = contracts[0]
    static_options = _normalized_options(contract)
    try:
        modern = enum_runtime_contract(contract)
    except (TypeError, ValueError) as exc:
        return _result(
            field,
            note=str(exc),
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    if modern is None:
        return _result(
            field,
            note="V3 禁止使用旧枚举兼容契约",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    fault = enum_contract_fault(modern)
    if fault is not None:
        return _result(
            field,
            note=fault.message,
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )

    refs = [item for item in cap.get("request_refs") or [] if isinstance(item, dict)]
    if any(not item.get("step_uuid") for item in refs):
        return _result(
            field,
            note="V3 request_ref 缺少 canonical step_uuid",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    all_steps = [step for step in api_request.get("steps") or [] if isinstance(step, dict)]
    try:
        by_uuid, by_id = _step_indexes(all_steps)
        option_steps = [
            _resolve_ref_step(item, by_uuid, by_id)
            for item in refs if item.get("usage") == "option_source"
        ]
    except ValueError as exc:
        return _result(
            field,
            note=str(exc),
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    declared_step_id = str(contract.get("source_step_id") or "")
    declared_step_uuid = str(contract.get("source_step_uuid") or "")
    source_query = modern.evidence.source_query
    if source_query is not None and not declared_step_uuid:
        return _result(
            field,
            note="动态 V3 枚举契约缺少 canonical source_step_uuid",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    candidates = [
        step for step in all_steps
        if declared_step_uuid
        and str(step.get("step_uuid") or "") == declared_step_uuid
    ]
    if not candidates:
        if static_options:
            return _result(
                field,
                options=static_options,
                note="选项来自 V3 冻结静态证据",
                capability=capability_name,
            )
        return _result(
            field,
            note="该字段没有可执行的 V3 选项来源",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="option_source_missing",
        )
    if len(candidates) != 1:
        return _result(
            field,
            note="V3 选项来源不唯一",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    source = deepcopy(candidates[0])
    source_step_id = str(source.get("step_id") or "")
    source_step_uuid = str(source.get("step_uuid") or "")
    if source_step_uuid != declared_step_uuid:
        return _result(
            field,
            note="枚举来源 step_uuid 不匹配",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    if declared_step_id and source_step_id != declared_step_id:
        return _result(
            field,
            note="枚举来源 step_id 不匹配",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    if source_query is not None:
        request_definition_id = str(source_query.request_definition_id or "")
        captured_request_ids = {
            str(source.get("request_definition_id") or ""),
            str(source.get("request_id") or ""),
        }
        captured_request_ids.discard("")
        if request_definition_id and request_definition_id not in captured_request_ids:
            return _result(
                field,
                note="枚举 request_definition 与 source_step_uuid 不匹配",
                capability=capability_name,
                ok=False,
                blocked=True,
                stage="invalid_contract",
            )
    original_source = candidates[0]
    if not any(original_source is value for value in option_steps):
        return _result(
            field,
            note="选项请求没有被 capability 声明为 option_source",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    if str(source.get("method") or "GET").upper() not in {"GET", "HEAD"}:
        return _result(
            field,
            note="V3 选项来源必须是只读请求",
            capability=capability_name,
            ok=False,
            blocked=True,
            stage="invalid_contract",
        )
    if source_query is not None:
        query = source_query
        if query.method != str(source.get("method") or "GET").upper():
            return _result(
                field,
                note="V3 枚举查询方法与录制请求不一致",
                capability=capability_name,
                ok=False,
                blocked=True,
                stage="invalid_contract",
            )

    option_result = await execute_recording_workflow(
        {**api_request, "steps": [source], "capabilities": [cap]},
        {},
        base_url=base_url,
        credential_headers=credential_headers,
        runtime_context=runtime_context,
        sender=sender,
        send=True,
        allow_private_networks=allow_private_networks,
        address_resolver=address_resolver,
    )
    if option_result.get("ok") is not True:
        malformed_blocked = (
            "blocked" in option_result
            and not isinstance(option_result.get("blocked"), bool)
        )
        return _result(
            field,
            note=str(option_result.get("detail") or "V3 实时选项请求失败"),
            capability=capability_name,
            ok=False,
            blocked=option_result.get("blocked") is True or malformed_blocked,
            stage="option_source",
            source_result=option_result,
        )
    extraction = contract
    if source_query is not None:
        query = source_query
        extraction = {
            "options_path": query.pagination.records_path if query.pagination else "",
            "label_path": query.label_path,
            "value_path": query.value_path,
        }
    dynamic = extract_dynamic_options(option_result.get("output"), extraction)
    if dynamic:
        return _result(
            field,
            options=dynamic,
            note="选项来自 V3 实时证据接口",
            capability=capability_name,
        )
    if static_options:
        return _result(
            field,
            options=static_options,
            note="实时接口未返回选项，使用 V3 冻结证据",
            capability=capability_name,
        )
    return _result(
        field,
        note="V3 实时选项接口未返回可用选项",
        capability=capability_name,
    )


__all__ = ["list_recording_field_options"]
