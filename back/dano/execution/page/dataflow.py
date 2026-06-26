"""Infer transaction dataflow from a recorded page request.

This module is the boundary between browser capture and request compilation.
It turns captured writes, reads and form samples into Transaction IR plus the
UI-facing field/select suggestions used by the recorder.
"""

from __future__ import annotations

from typing import Any

from dano.execution.page.request_capture import (
    flatten_body,
    fold_array_select_fields,
    fold_derived_mirror_fields,
    infer_success_rule,
    suggest_identity,
    suggest_select_names,
    suggest_selects,
    suggest_workflow_steps,
)
from dano.execution.page.transaction_ir import (
    BindingSpec,
    ConstantSpec,
    IdentitySpec,
    InputSpec,
    SourceSpec,
    StepSpec,
    TransactionIR,
    ir_to_dict,
    request_path,
    stable_source_id,
)


def _field_name(f: dict) -> str:
    return str(f.get("suggest_name") or f.get("key") or f.get("path") or "").strip()


def _select_source_id(s: dict) -> str:
    return stable_source_id(s.get("source_url"), s.get("value_key"), s.get("label_key"))


def _source_specs(selects: list[dict]) -> list[SourceSpec]:
    out: dict[str, SourceSpec] = {}
    for s in selects or []:
        url = s.get("source_url")
        if not url:
            continue
        sid = _select_source_id(s)
        if sid in out:
            continue
        out[sid] = SourceSpec(
            id=sid,
            kind="http_list",
            url=url,
            value_key=str(s.get("value_key") or ""),
            label_key=str(s.get("label_key") or ""),
            count=s.get("count"),
            options=list(s.get("options") or []),
            option_filter=dict(s.get("option_filter") or {}) or None,
        )
    return list(out.values())


def _input_type(field: dict, select: dict | None) -> str:
    if select and select.get("kind") == "array":
        return "array"
    if select:
        return "select"
    return str(field.get("type") or "string")


def _submit_mode(select: dict | None) -> str:
    if not select:
        return "raw"
    if select.get("kind") == "array":
        return "value[]"
    return "value"


def _binding_for(field: dict, select: dict | None) -> BindingSpec:
    name = _field_name(field)
    path = str(field.get("path") or "")
    if select and select.get("kind") == "array":
        template = select.get("item_template") if isinstance(select.get("item_template"), dict) else None
        return BindingSpec(
            input=name,
            target_path=str(select.get("array_path") or select.get("path") or path),
            mode="expand_array",
            source_id=_select_source_id(select),
            target_key=select.get("target_key") or select.get("value_key"),
            item_template=template,
            expand_fields=list(template.keys()) if template else [],
        )
    if select:
        return BindingSpec(
            input=name,
            target_path=path,
            mode="select_value",
            source_id=_select_source_id(select),
            target_key=select.get("value_key"),
        )
    return BindingSpec(input=name, target_path=path, mode="direct")


def build_transaction_ir(*, chosen: dict, candidates: list[dict] | None, fields: list[dict],
                         selects: list[dict], identity: list[dict], samples: dict | None,
                         reads: list[dict] | None = None, mirrors: list[dict] | None = None) -> dict:
    sel_by_path = {s.get("path"): s for s in selects or [] if s.get("path")}
    id_paths = {i.get("path") for i in identity or []}
    inputs: list[InputSpec] = []
    bindings: list[BindingSpec] = []
    constants: list[ConstantSpec] = []
    for f in fields or []:
        path = f.get("path")
        if not path:
            continue
        sel = sel_by_path.get(path)
        selected = bool(sel or f.get("suggest_param")) and path not in id_paths
        if selected:
            inp = InputSpec(
                name=_field_name(f),
                path=path,
                type=_input_type(f, sel),
                required=bool(f.get("required", True)),
                sample=f.get("value"),
                source_id=_select_source_id(sel) if sel else None,
                submit_mode=_submit_mode(sel),
                confidence=f.get("confidence"),
                selected_default=True,
                evidence=[f"request://body.{path}"],
            )
            inputs.append(inp)
            bindings.append(_binding_for(f, sel))
        elif path not in id_paths:
            constants.append(ConstantSpec(path=path, value=f.get("value")))
    steps: list[StepSpec] = []
    suggested = set(suggest_workflow_steps(candidates or [], samples or {}))
    for i, c in enumerate(candidates or []):
        steps.append(StepSpec(
            idx=i,
            method=(c.get("method") or "POST").upper(),
            path=request_path(c.get("url")),
            role="selected_write" if i in suggested else "write",
        ))
    derived: list[dict] = []
    for s in selects or []:
        if s.get("kind") != "array":
            continue
        for d in s.get("derived_count_paths") or []:
            derived.append({"kind": "array_count", "source_path": s.get("path"),
                            "target_path": d.get("path"), "param": s.get("param")})
    for m in mirrors or []:
        derived.append({"kind": "mirror", "source_path": m.get("source_path"),
                        "target_path": m.get("target_path"), "param": m.get("param"),
                        "style": m.get("style")})
    success_evidence: list[dict] = []
    if chosen.get("response_json") is not None:
        success_evidence.append({"json": chosen.get("response_json")})
    success_evidence.extend([r for r in (reads or []) if isinstance(r, dict)])
    ir = TransactionIR(
        method=(chosen.get("method") or "POST").upper(),
        url=chosen.get("url") or "",
        path=request_path(chosen.get("url")),
        inputs=inputs,
        sources=_source_specs(selects),
        bindings=bindings,
        constants=constants,
        identity=[
            IdentitySpec(path=i.get("path", ""), source=i.get("source", ""),
                         evidence=list(i.get("evidence") or []))
            for i in identity or []
        ],
        derived=derived,
        steps=steps,
        success=infer_success_rule(success_evidence) or {},
        capture={
            "field_count": len(fields or []),
            "select_count": len(selects or []),
            "identity_count": len(identity or []),
        },
    )
    return ir_to_dict(ir)


def infer_request_transaction(chosen: dict, candidates: list[dict] | None, samples: dict | None,
                              reads: list[dict] | None = None, storage: dict | None = None,
                              required_labels: set | None = None) -> dict[str, Any]:
    """Infer field suggestions and Transaction IR for one chosen write request."""
    post_data = chosen.get("post_data")
    fields = flatten_body(post_data, samples or {}, required_labels)
    selects = suggest_selects(post_data, reads or [], samples or {})
    sel_names = suggest_select_names(selects, samples or {})
    for f in fields:
        if f.get("path") in sel_names:
            f["suggest_name"] = sel_names[f["path"]]
    fields, selects = fold_array_select_fields(post_data, fields, selects)
    fields, mirrors = fold_derived_mirror_fields(post_data, fields)
    identity = suggest_identity(post_data, storage, samples or {})
    ir = build_transaction_ir(
        chosen=chosen,
        candidates=candidates or [],
        fields=fields,
        selects=selects,
        identity=identity,
        samples=samples or {},
        reads=reads or [],
        mirrors=mirrors,
    )
    return {"fields": fields, "selects": selects, "identity": identity,
            "suggested_steps": suggest_workflow_steps(candidates or [], samples or {}),
            "derived_mirrors": mirrors, "transaction_ir": ir}
