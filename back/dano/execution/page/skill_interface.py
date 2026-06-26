"""Public Skill interface derived from recorded request skills.

This layer is for callers and exported Agent Skills. Runtime execution still
uses the existing ``api_request`` shape; the interface only describes how the
caller should supply fields and how Dano will bind them into the captured
request.
"""

from __future__ import annotations

import copy
from typing import Any

from dano.execution.page.request_capture import _leaf_paths
from dano.execution.page.transaction_ir import stable_source_id


SKILL_INTERFACE_VERSION = "skill-interface/v1"


def _requests(api_request: dict | None) -> list[tuple[int, dict]]:
    apir = api_request or {}
    steps = apir.get("steps")
    if steps:
        return [(i, s or {}) for i, s in enumerate(steps)]
    return [(0, apir)]


def _last_request(api_request: dict | None) -> dict:
    reqs = _requests(api_request)
    return reqs[-1][1] if reqs else {}


def _params(api_request: dict | None) -> list[str]:
    apir = api_request or {}
    params = list(apir.get("params") or [])
    if not params and apir.get("steps"):
        params = list((_last_request(apir)).get("params") or [])
    return list(dict.fromkeys(params))


def _field_types(api_request: dict | None) -> dict:
    apir = api_request or {}
    out = dict(apir.get("field_types") or {})
    if not out and apir.get("steps"):
        out = dict((_last_request(apir)).get("field_types") or {})
    return out


def _json_type(declared: str | None) -> str:
    if declared in {"array", "object", "number", "integer", "boolean"}:
        return declared
    return "string"


def _selects(api_request: dict | None) -> list[dict]:
    out: list[dict] = []
    for step_idx, req in _requests(api_request):
        for s in req.get("selects") or []:
            if isinstance(s, dict):
                out.append({**s, "step": step_idx})
    return out


def _source_id(select: dict) -> str:
    return str(select.get("source_id") or stable_source_id(
        select.get("source_url"), select.get("value_key"), select.get("label_key")))


def _placeholder_bindings(api_request: dict | None, select_params: set[str]) -> list[dict]:
    bindings: list[dict] = []
    for step_idx, req in _requests(api_request):
        templ = req.get("body_template")
        if not isinstance(templ, (dict, list)):
            continue
        for path, tokens, value, _raw in _leaf_paths(templ):
            if not (isinstance(value, str) and value.startswith("{{") and value.endswith("}}")):
                continue
            name = value[2:-2]
            if name in select_params:
                continue
            bindings.append({
                "input": name,
                "target_path": path,
                "target_tokens": tokens,
                "mode": "direct",
                "step": step_idx,
            })
    return bindings


def _select_binding(select: dict) -> dict:
    mode = "expand_array" if select.get("kind") == "array" else "select_value"
    return {
        "input": select.get("param"),
        "target_path": select.get("array_path") or select.get("path"),
        "target_tokens": select.get("array_tokens") or select.get("tokens"),
        "mode": mode,
        "source_id": _source_id(select),
        "target_key": select.get("target_key") or select.get("value_key"),
        "paired_id_path": select.get("id_path"),
        "expand_fields": list((select.get("item_template") or {}).keys())
        if isinstance(select.get("item_template"), dict) else list(select.get("expand_fields") or []),
        "step": select.get("step", 0),
    }


def _source_schema(selects: list[dict]) -> dict:
    sources: dict[str, dict] = {}
    for s in selects:
        sid = _source_id(s)
        src = sources.setdefault(sid, {
            "id": sid,
            "kind": "http_list",
            "url": s.get("source_url") or "",
            "fields": [],
            "submit_modes": [],
            "value_key": s.get("value_key") or "",
            "label_key": s.get("label_key") or "",
            "count": s.get("count"),
            "has_runtime_source": bool(s.get("source_url")),
        })
        if s.get("param") and s.get("param") not in src["fields"]:
            src["fields"].append(s.get("param"))
        mode = s.get("submit_mode") or ("value[]" if s.get("kind") == "array" else "value")
        if mode not in src["submit_modes"]:
            src["submit_modes"].append(mode)
        if s.get("option_filter"):
            src["option_filter"] = dict(s.get("option_filter") or {})
        if s.get("evidence"):
            src["evidence"] = list(s.get("evidence") or [])
    return sources


def _derived(api_request: dict | None, selects: list[dict]) -> list[dict]:
    apir = api_request or {}
    last = _last_request(apir)
    out = copy.deepcopy(apir.get("derived_fields") or last.get("derived_fields") or [])
    seen = {
        (d.get("kind") or "mirror", d.get("target_path"), d.get("source_path"), d.get("input"))
        for d in out if isinstance(d, dict)
    }
    for s in selects:
        if s.get("kind") != "array":
            continue
        for d in s.get("derived_count_paths") or []:
            item = {
                "kind": "array_count",
                "input": s.get("param"),
                "source_path": s.get("array_path") or s.get("path"),
                "target_path": d.get("path"),
                "target_tokens": d.get("tokens"),
                "step": s.get("step", 0),
            }
            key = (item["kind"], item["target_path"], item["source_path"], item["input"])
            if item["target_path"] and key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _input_schema(params: list[str], field_types: dict, selects: list[dict],
                  required_fields: list[str] | None) -> dict:
    sel_by_param = {s.get("param"): s for s in selects if s.get("param")}
    required = list(dict.fromkeys(required_fields if required_fields is not None else params))
    props: dict[str, dict] = {}
    for name in params:
        declared = field_types.get(name)
        prop: dict[str, Any] = {"type": _json_type(declared)}
        if declared == "enum":
            prop.update({"type": "string", "format": "name-ref"})
        if declared == "array":
            prop.update({"type": "array", "items": {"type": "string"}, "format": "name-ref-list"})
        sel = sel_by_param.get(name)
        if sel:
            prop["x-source-id"] = _source_id(sel)
            prop["x-submit-mode"] = sel.get("submit_mode") or ("value[]" if sel.get("kind") == "array" else "value")
            prop["x-option-label"] = "label"
            prop["x-option-value"] = "value"
            prop["x-options-source"] = bool(sel.get("source_url"))
        props[name] = prop
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def build_skill_interface(api_request: dict | None, *,
                          required_fields: list[str] | None = None) -> dict:
    """Build a stable public interface from the current api_request.

    The function intentionally avoids credential values, request body values and
    option labels. Those already live in runtime/private request metadata.
    """
    apir = api_request or {}
    params = _params(apir)
    ftypes = _field_types(apir)
    sels = _selects(apir)
    select_params = {s.get("param") for s in sels if s.get("param")}
    bindings = _placeholder_bindings(apir, select_params)
    bindings.extend(_select_binding(s) for s in sels if s.get("param"))
    last = _last_request(apir)
    tx_ir = apir.get("transaction_ir") if isinstance(apir.get("transaction_ir"), dict) else {}
    capture = copy.deepcopy((tx_ir or {}).get("capture") or {})
    return {
        "version": SKILL_INTERFACE_VERSION,
        "input_schema": _input_schema(params, ftypes, sels, required_fields),
        "source_schema": _source_schema(sels),
        "bindings": bindings,
        "identity": copy.deepcopy(apir.get("identity") or last.get("identity") or []),
        "derived": _derived(apir, sels),
        "success": copy.deepcopy(apir.get("success_rule") or last.get("success_rule") or {}),
        "provenance": {
            "transaction_ir_version": (tx_ir or {}).get("version"),
            "capture_hash": capture.get("capture_hash"),
            "trace_hash": capture.get("trace_hash"),
            "write_event": capture.get("write_event"),
        },
    }
