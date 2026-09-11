#!/usr/bin/env python3
"""Frozen flow: run recorded routes from CONTRACT.json. Do not rewrite."""

from __future__ import annotations

import json
import sys

import format_list
import runtime


def _print(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(code)


def _help():
    contract = runtime.load_contract()
    return {
        "usage": "python3 scripts/flow.py --route <id> --input-json '{...}' [--confirm]",
        "list_options": "python3 scripts/flow.py --list-options <capability_id> <field>",
        "routes": [
            {"id": item.get("route_id"), "title": item.get("title"), "steps": item.get("steps")}
            for item in contract.get("routes") or []
        ],
        "default_route": "default",
    }


def main(argv):
    if "--help" in argv or not argv:
        _print(_help())
    if "--list-routes" in argv:
        _print({"routes": _help()["routes"]})
    if "--list-options" in argv:
        idx = argv.index("--list-options")
        cap = argv[idx + 1] if idx + 1 < len(argv) else ""
        field = argv[idx + 2] if idx + 2 < len(argv) else ""
        try:
            _print({"ok": True, "capability_id": cap, "field": field, "options": runtime.list_field_options(cap, field)})
        except Exception as exc:
            code = 2 if getattr(exc, "code", "") == "AuthExpired" else 1
            _print({"ok": False, "error": str(exc), "code": getattr(exc, "code", "")}, code)
    route_id = "default"
    if "--route" in argv:
        route_id = argv[argv.index("--route") + 1]
    if "--capability" in argv:
        route_id = argv[argv.index("--capability") + 1]
    raw = "{}"
    if "--input-json" in argv:
        raw = argv[argv.index("--input-json") + 1]
    try:
        inputs = json.loads(raw)
    except json.JSONDecodeError as exc:
        _print({"ok": False, "error": f"input-json 非法: {exc}"}, 1)
    confirm = "--confirm" in argv
    try:
        contract = runtime.load_contract()
        ids = {str(item.get("capability_id") or "") for item in contract.get("capabilities") or []}
        if route_id in ids:
            payload = {"ok": True, "results": [{"capability_id": route_id, "result": runtime.execute_capability(route_id, inputs, confirm=confirm)}]}
        else:
            payload = runtime.run_route(route_id, inputs, confirm=confirm)
        if payload.get("ok"):
            format_list.attach_tables(payload)
        _print(payload, 0 if payload.get("ok") else 1)
    except Exception as exc:
        code = 2 if getattr(exc, "code", "") == "AuthExpired" else 1
        _print({"ok": False, "error": str(exc), "code": getattr(exc, "code", "")}, code)


if __name__ == "__main__":
    main(sys.argv[1:])
