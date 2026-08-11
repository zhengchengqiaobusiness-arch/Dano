"""Minimal standalone consumer for exported self-contained skill packages."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def _contract(package: Path) -> dict:
    path = package / "references" / "CONTRACT.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("protocol") != "dano.skill_package.contract.v1":
        raise ValueError("unsupported or missing package contract")
    if not isinstance(data.get("capabilities"), list):
        raise ValueError("package contract has no capabilities")
    return data


def _script(package: Path, relative: str) -> Path:
    root = package.resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError(f"invalid package script: {relative}")
    return path


def _invoke(package: Path, relative: str, inputs: dict) -> dict:
    path = _script(package, relative)
    completed = subprocess.run(
        [sys.executable, str(path), "--input-json", json.dumps(inputs, ensure_ascii=False)],
        cwd=path.parent,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        output = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        output = None
    return {
        "ok": completed.returncode == 0 and isinstance(output, dict) and output.get("ok") is True,
        "returncode": completed.returncode,
        "output": output,
        "stderr": completed.stderr.strip()[-1000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Consume an exported self-contained skill package")
    commands = parser.add_subparsers(dest="command", required=True)
    list_command = commands.add_parser("list", help="List package capabilities and input schemas")
    list_command.add_argument("package")
    run_command = commands.add_parser("run", help="Run one capability and its write verifier")
    run_command.add_argument("package")
    run_command.add_argument("capability")
    run_command.add_argument("--input-json", default="{}")
    args = parser.parse_args()

    try:
        package = Path(args.package)
        contract = _contract(package)
        if args.command == "list":
            _emit({"ok": True, "skill": contract.get("skill"), "capabilities": contract["capabilities"]})
            return 0
        inputs = json.loads(args.input_json)
        if not isinstance(inputs, dict):
            raise ValueError("--input-json must be an object")
        capability = next(
            (item for item in contract["capabilities"] if item.get("name") == args.capability),
            None,
        )
        if capability is None:
            raise ValueError(f"unknown capability: {args.capability}")
        execution = _invoke(package, str(capability["script"]), inputs)
        verification = None
        if execution["ok"] and capability.get("requires_verify"):
            verification = _invoke(package, str(capability["verify_script"]), inputs)
        ok = execution["ok"] and (verification is None or verification["ok"])
        _emit({
            "ok": ok,
            "capability": args.capability,
            "execution": execution,
            "verification": verification,
        })
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 - stable CLI failure envelope
        _emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
