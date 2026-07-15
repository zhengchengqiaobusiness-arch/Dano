"""Select and execute one explicit V3 capability."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from dano_recording.enum_resolver import enum_contract_fault

from .fact_checker import schema_issues
from .option_resolver import (
    enum_runtime_contract,
    resolve_evidence_choice,
)
from .safety import capability_requires_confirmation
from .workflow_executor import AddressResolver, AsyncSender, execute_recording_workflow
from .request_builder import CredentialHeaders


def _select(api_request: dict, name: str | None) -> dict:
    capabilities = [item for item in api_request.get("capabilities") or [] if isinstance(item, dict)]
    if name:
        stable = [
            item for item in capabilities
            if str(item.get("capability_uuid") or "") == name
        ]
        if len(stable) == 1:
            return stable[0]
        if len(stable) > 1:
            raise ValueError(f"ambiguous capability UUID: {name}")
        found = next((item for item in capabilities if name in {
            item.get("name"), item.get("kind"), item.get("capability_id")
        }), None)
        if found is None:
            raise ValueError(f"unknown capability: {name}")
        return found
    if len(capabilities) != 1:
        raise ValueError("an explicit capability is required")
    return capabilities[0]


def _step_indexes(steps: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_uuid: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for step in steps:
        step_uuid = str(step.get("step_uuid") or "")
        step_id = str(step.get("step_id") or "")
        if not step_uuid:
            raise ValueError("V3 runtime step lacks canonical step_uuid")
        if step_uuid in by_uuid:
            raise ValueError(f"duplicate runtime step UUID: {step_uuid}")
        by_uuid[step_uuid] = step
        if step_id:
            if step_id in by_id:
                raise ValueError(f"duplicate runtime step id: {step_id}")
            by_id[step_id] = step
    return by_uuid, by_id


def _resolve_ref_step(
    ref: dict[str, Any],
    by_uuid: dict[str, dict],
    by_id: dict[str, dict],
) -> dict[str, Any]:
    step_uuid = str(ref.get("step_uuid") or "")
    step_id = str(ref.get("step_id") or "")
    if not step_uuid:
        raise ValueError("V3 runtime reference lacks canonical step_uuid")
    step = by_uuid.get(step_uuid)
    if step is None:
        raise ValueError(f"declared runtime step UUID is missing: {step_uuid}")
    if step_id and by_id.get(step_id) is not step:
        raise ValueError(
            f"runtime step UUID/id reference mismatch: {step_uuid} / {step_id}"
        )
    return step


def _capability_steps(
    cap: dict[str, Any],
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve execute/option refs UUID-first and reject inconsistent aliases."""

    if not cap.get("capability_uuid"):
        raise ValueError("V3 runtime capability lacks canonical capability_uuid")
    by_uuid, by_id = _step_indexes(steps)
    declared_uuids = [str(value) for value in cap.get("step_uuids") or [] if str(value)]
    declared_ids = [str(value) for value in cap.get("step_ids") or [] if str(value)]
    refs = [item for item in cap.get("request_refs") or [] if isinstance(item, dict)]
    if not declared_uuids:
        raise ValueError("V3 runtime capability lacks canonical step_uuids")
    execute_steps = [
        _resolve_ref_step(
            {
                "step_uuid": step_uuid,
                "step_id": declared_ids[index] if index < len(declared_ids) else "",
            },
            by_uuid,
            by_id,
        )
        for index, step_uuid in enumerate(declared_uuids)
    ]
    if declared_ids and len(declared_ids) != len(declared_uuids):
        raise ValueError("runtime capability step UUID/id list lengths do not match")
    if any(not ref.get("step_uuid") for ref in refs):
        raise ValueError("V3 runtime request_ref lacks canonical step_uuid")
    option_steps = [
        _resolve_ref_step(ref, by_uuid, by_id)
        for ref in refs if ref.get("usage") == "option_source"
    ]
    return execute_steps, option_steps


async def execute_recording_capability(
    api_request: dict,
    fields: dict[str, Any],
    *,
    capability: str | None,
    confirm: bool,
    base_url: str,
    credential_headers: CredentialHeaders | None = None,
    runtime_context: dict[str, Any] | None = None,
    sender: AsyncSender | None = None,
    dry_run: bool = False,
    allow_private_networks: bool = False,
    address_resolver: AddressResolver | None = None,
) -> dict[str, Any]:
    if api_request.get("recording_engine") != "playwright_v3":
        raise ValueError("V3 capability runtime requires recording_engine=playwright_v3")
    verification = str(api_request.get("verification_status") or "")
    if not dry_run and not (
        verification == "verified"
        and api_request.get("direct_call_enabled") is True
    ):
        return {
            "ok": False,
            "blocked": True,
            "stage": "unverified_contract",
            "detail": "only a published verified recording revision can be called directly",
            "contract_faults": list(api_request.get("contract_faults") or []),
        }
    try:
        cap = _select(api_request, capability)
    except ValueError as exc:
        return {
            "ok": False,
            "blocked": True,
            "stage": "capability_not_found",
            "detail": str(exc),
        }
    all_steps = [step for step in api_request.get("steps") or [] if isinstance(step, dict)]
    try:
        steps, option_source_steps = _capability_steps(cap, all_steps)
    except ValueError as exc:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_contract",
            "detail": str(exc),
            "capability": cap.get("capability_uuid") or cap.get("name") or cap.get("kind"),
        }
    if not steps:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_contract",
            "detail": "capability contains no executable request",
            "capability": cap.get("capability_uuid") or cap.get("name") or cap.get("kind"),
        }
    public_fields = {str(key): value for key, value in fields.items() if not str(key).startswith("__")}
    input_issues = schema_issues(public_fields, cap.get("input_schema") or {}, "input")
    if input_issues:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_input",
            "detail": "; ".join(input_issues),
            "input_issues": input_issues,
            "capability": cap.get("name") or cap.get("kind"),
        }
    if cap.get("execution_enabled") is False and not dry_run:
        return {
            "ok": False,
            "blocked": True,
            "stage": "execution_disabled",
            "capability": cap.get("name") or cap.get("kind"),
        }
    if capability_requires_confirmation(cap, steps) and not confirm and not dry_run:
        return {
            "ok": False,
            "blocked": True,
            "stage": "confirmation_required",
            "capability": cap.get("name") or cap.get("kind"),
            "requires_confirmation": True,
        }
    contracts = [dict(item) for item in cap.get("choice_contracts") or [] if isinstance(item, dict)]
    modern_contracts: list[dict[str, Any]] = []
    try:
        for contract in contracts:
            parsed = enum_runtime_contract(contract)
            if parsed is None:
                raise ValueError(
                    "legacy choice contracts are forbidden in the V3 direct runtime"
                )
            fault = enum_contract_fault(parsed)
            if fault is not None:
                raise ValueError(fault.message)
            if str(contract.get("public_name") or contract.get("field") or "") in fields:
                if (
                    parsed.evidence.source_query is not None
                    and not str(contract.get("source_step_uuid") or "")
                ):
                    raise ValueError(
                        "dynamic V3 enum contract lacks canonical source_step_uuid"
                    )
                modern_contracts.append(contract)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_contract",
            "detail": str(exc),
            "capability": cap.get("name") or cap.get("kind"),
        }
    option_evidence: list[dict[str, Any]] = []
    try:
        effective = dict(fields)
        for contract in modern_contracts:
            name = str(contract.get("public_name") or contract.get("field") or "")
            if not name or name not in effective:
                continue
            parsed = enum_runtime_contract(contract)
            assert parsed is not None
            source_query = parsed.evidence.source_query

            async def fetch_enum(request: dict[str, Any]) -> Any:
                declared_step_uuid = str(contract.get("source_step_uuid") or "")
                if not declared_step_uuid:
                    raise ValueError(
                        "dynamic V3 enum contract lacks canonical source_step_uuid"
                    )
                candidates = [
                    step for step in all_steps
                    if str(step.get("step_uuid") or "") == declared_step_uuid
                ]
                if len(candidates) != 1:
                    raise ValueError("enum resolver source_step_uuid is missing or ambiguous")
                source_step = candidates[0]
                source_step_id = str(source_step.get("step_id") or "")
                declared_step_id = str(contract.get("source_step_id") or "")
                if declared_step_id and source_step_id != declared_step_id:
                    raise ValueError("enum resolver request does not match its source step")
                request_definition_id = str(
                    request.get("request_definition_id")
                    or (source_query.request_definition_id if source_query else "")
                )
                captured_request_ids = {
                    str(source_step.get("request_definition_id") or ""),
                    str(source_step.get("request_id") or ""),
                }
                captured_request_ids.discard("")
                if request_definition_id and request_definition_id not in captured_request_ids:
                    raise ValueError(
                        "enum resolver request definition does not match its source step UUID"
                    )
                if not any(source_step is value for value in option_source_steps):
                    raise ValueError("enum resolver request is not an option-source capability reference")
                method = str(request.get("method") or "GET").upper()
                captured_method = str(source_step.get("method") or "GET").upper()
                if method != captured_method or method not in {"GET", "HEAD"}:
                    raise ValueError("enum resolver may execute only its captured read-only request")
                for key in ("url", "path"):
                    requested = str(request.get(key) or "")
                    captured = str(source_step.get(key) or "")
                    if requested and captured and requested != captured:
                        raise ValueError("enum resolver cannot change its captured endpoint")
                rendered_step = deepcopy(source_step)
                if "query_template" in request or "query" in request:
                    rendered_step["query_template"] = deepcopy(
                        request.get("query_template", request.get("query"))
                    )
                if "body_template" in request or "body" in request:
                    rendered_step["body_template"] = deepcopy(
                        request.get("body_template", request.get("body"))
                    )
                option_result = await execute_recording_workflow(
                    {**api_request, "steps": [rendered_step], "capabilities": [cap]},
                    effective,
                    base_url=base_url,
                    credential_headers=credential_headers,
                    runtime_context=runtime_context,
                    sender=sender,
                    send=True,
                    allow_private_networks=allow_private_networks,
                    address_resolver=address_resolver,
                )
                if option_result.get("ok") is not True:
                    raise ValueError("enum option-source request failed")
                return option_result.get("output")

            resolved, resolutions = await resolve_evidence_choice(
                contract,
                effective[name],
                fetcher=fetch_enum if source_query is not None else None,
            )
            effective[name] = resolved
            option_evidence.append({
                "field": name,
                "source_step_id": contract.get("source_step_id"),
                "count": len(resolutions),
                "pages_fetched": sum(item.pages_fetched for item in resolutions),
                "matched_by": [item.matched_by for item in resolutions],
            })
    except ValueError as exc:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_input",
            "detail": str(exc),
            "input_issues": [str(exc)],
            "capability": cap.get("name") or cap.get("kind"),
        }
    selected = {**api_request, "steps": steps, "capabilities": [cap]}
    result = await execute_recording_workflow(
        selected,
        effective,
        base_url=base_url,
        credential_headers=credential_headers,
        runtime_context=runtime_context,
        sender=sender,
        send=not dry_run,
        allow_private_networks=allow_private_networks,
        address_resolver=address_resolver,
    )
    result["capability"] = cap.get("name") or cap.get("kind")
    if result.get("ok") is True:
        output_issues = schema_issues(result.get("output"), cap.get("output_schema") or {}, "output")
        if output_issues:
            result.update({
                "ok": False,
                "blocked": True,
                "stage": "invalid_output",
                "detail": "; ".join(output_issues),
                "output_issues": output_issues,
            })
    if option_evidence:
        result["option_sources"] = option_evidence
    return result
