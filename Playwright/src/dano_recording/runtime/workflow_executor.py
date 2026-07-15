"""HTTP workflow executor for V3 assets (never imports the legacy recorder/runtime)."""

from __future__ import annotations

import asyncio
import inspect
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from .fact_checker import response_success
from .request_builder import (
    BuiltRequest,
    CredentialHeaders,
    MissingRuntimeCredential,
    build_request,
)
from .safety import RuntimePolicy, safe_response_headers


class AsyncSender(Protocol):
    """Trusted/test transport injection boundary.

    Production execution uses the owned ``httpx`` client below so the exact
    DNS address accepted by the SSRF policy can be pinned to the socket.
    """

    async def request(self, method: str, url: str, **kwargs): ...  # noqa: ANN003


AddressResolver = Callable[[str, int], Sequence[str] | Awaitable[Sequence[str]]]


async def _system_resolver(hostname: str, port: int) -> Sequence[str]:
    rows = await asyncio.get_running_loop().getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(dict.fromkeys(str(row[4][0]) for row in rows if row[4]))


async def _validate_network_target(
    policy: RuntimePolicy,
    url: str,
    resolver: AddressResolver,
) -> tuple[str, ...]:
    parsed = urlsplit(url)
    hostname = str(parsed.hostname or "")
    port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    resolved = resolver(hostname, port)
    addresses = await resolved if inspect.isawaitable(resolved) else resolved
    validated = tuple(dict.fromkeys(str(value) for value in addresses))
    policy.validate_resolved_addresses(url, validated)
    return validated


def _pinned_target(url: str, address: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Route to a validated address while retaining HTTP and TLS identity."""

    parsed = urlsplit(url)
    hostname = str(parsed.hostname or "").encode("idna").decode("ascii")
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    port = int(parsed.port or default_port)
    ip_netloc = f"[{address}]" if ":" in address else address
    if port != default_port:
        ip_netloc = f"{ip_netloc}:{port}"
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if port != default_port:
        host_header = f"{host_header}:{port}"
    dispatch_url = urlunsplit((parsed.scheme, ip_netloc, parsed.path, parsed.query, parsed.fragment))
    extensions = {"sni_hostname": hostname} if parsed.scheme.lower() == "https" else {}
    return dispatch_url, {"Host": host_header}, extensions


async def _send(
    sender: AsyncSender,
    request: BuiltRequest,
    *,
    pinned_address: str | None = None,
) -> dict[str, Any]:
    dispatch_url = request.url
    headers = dict(request.headers)
    extensions: dict[str, Any] = {}
    if pinned_address is not None:
        dispatch_url, identity_headers, extensions = _pinned_target(request.url, pinned_address)
        headers.update(identity_headers)
    kwargs: dict[str, Any] = {"headers": headers}
    if extensions:
        kwargs["extensions"] = extensions
    if request.form_body is not None:
        kwargs["data"] = request.form_body
    elif request.json_body is not None:
        kwargs["json"] = request.json_body
    response = await sender.request(request.method, dispatch_url, **kwargs)
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = {"text": response.text[:100_000]}
    return {
        "status": response.status_code,
        "headers": safe_response_headers(dict(response.headers)),
        "body": payload,
    }


def _schema_example(schema: Any) -> Any:
    """Build a non-sensitive dry-run value for downstream request rendering."""

    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {
            str(key): _schema_example(value)
            for key, value in (schema.get("properties") or {}).items()
            if isinstance(value, dict)
        }
    if kind == "array":
        return [_schema_example(schema.get("items") or {})]
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    if kind == "string":
        return "example"
    return None


async def execute_recording_workflow(
    api_request: dict,
    fields: dict[str, Any],
    *,
    base_url: str,
    credential_headers: CredentialHeaders | None = None,
    runtime_context: dict[str, Any] | None = None,
    sender: AsyncSender | None = None,
    send: bool = True,
    allow_private_networks: bool = False,
    address_resolver: AddressResolver | None = None,
) -> dict[str, Any]:
    if api_request.get("recording_engine") != "playwright_v3":
        raise ValueError("V3 runtime only accepts recording_engine=playwright_v3")
    verification = str(api_request.get("verification_status") or "")
    if send and not (
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
    steps = list(api_request.get("steps") or [api_request])
    if not steps or any(not isinstance(step, dict) for step in steps):
        raise ValueError("V3 workflow steps must be non-empty objects")
    if len(steps) > 1_000:
        raise ValueError("V3 workflow exceeds the 1000-step safety bound")
    step_uuids = [str(step.get("step_uuid") or "") for step in steps]
    if any(not value for value in step_uuids) or len(set(step_uuids)) != len(step_uuids):
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_contract",
            "detail": "V3 workflow requires unique canonical step_uuid values",
            "results": [],
        }
    policy = RuntimePolicy(
        recorded_origin=str(api_request.get("recorded_origin") or base_url),
        allow_http=api_request.get("allow_http") is True,
        allow_private_networks=allow_private_networks is True,
    )
    owned = sender is None
    timeout_s = min(max(float(api_request.get("timeout_s") or 60), 1.0), 300.0)
    client = sender or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
    outputs: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    try:
        for step in steps:
            try:
                request = build_request(
                    step,
                    fields=fields,
                    outputs=outputs,
                    base_url=base_url,
                    policy=policy,
                    credential_headers=credential_headers,
                    runtime_context=runtime_context,
                )
            except MissingRuntimeCredential as exc:
                return {
                    "ok": False,
                    "blocked": True,
                    "stage": "credential_required",
                    "step_id": str(step.get("step_id") or ""),
                    "step_uuid": str(step.get("step_uuid") or ""),
                    "detail": str(exc),
                    "results": results,
                }
            if not send:
                sample_output = _schema_example(step.get("response_schema") or {})
                result = {
                    "status": 0,
                    "headers": {},
                    "body": sample_output,
                    "ok": True,
                    "reason": "dry_run",
                    "dry_run": True,
                    "request": {
                        "method": request.method,
                        "url": request.url,
                        "has_body": request.json_body is not None or request.form_body is not None,
                    },
                }
            else:
                # Resolve immediately before dispatch.  Literal special IPs
                # were already rejected by RuntimePolicy; this closes the DNS
                # rebinding/metadata-hostname path for the owned HTTP client.
                resolver = address_resolver or (_system_resolver if owned else None)
                pinned_address: str | None = None
                if resolver is not None:
                    try:
                        addresses = await _validate_network_target(policy, request.url, resolver)
                        # Only the owned production transport can guarantee
                        # that the validated address is the connected address.
                        # Injected senders are a trusted/test-only boundary.
                        if owned:
                            pinned_address = addresses[0]
                    except ValueError as exc:
                        return {
                            "ok": False,
                            "blocked": True,
                            "stage": "unsafe_target",
                            "step_id": request.step_id,
                            "step_uuid": request.step_uuid,
                            "detail": str(exc),
                            "results": results,
                        }
                result = await _send(client, request, pinned_address=pinned_address)
                passed, reason = response_success(
                    int(result["status"]), result["body"], step.get("success_rule") or api_request.get("success_rule")
                )
                result.update({"ok": passed, "reason": reason})
                if not passed:
                    results.append(result)
                    return {
                        "ok": False,
                        "stage": "request",
                        "step_id": request.step_id,
                        "step_uuid": request.step_uuid,
                        "results": results,
                    }
            step_uuid = request.step_uuid
            outputs[step_uuid] = result.get("body")
            # ``step_id`` remains a non-authoritative template alias for
            # already compiled dependency expressions.  Result identity is
            # always the canonical UUID and never an array index.
            if request.step_id and request.step_id != step_uuid:
                outputs[request.step_id] = result.get("body")
            results.append({
                "step_uuid": step_uuid,
                "step_id": request.step_id,
                **result,
            })
        return {"ok": True, "results": results, "output": results[-1].get("body") if results else None}
    finally:
        if owned:
            await client.aclose()
