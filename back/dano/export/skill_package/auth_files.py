"""Write runtime/auth files into a packed Skill. Transport only; no business inference."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def package_root_from_script(script_path: Path) -> Path:
    return Path(script_path).resolve().parent.parent


def auth_file(pkg: Path) -> Path:
    return Path(pkg) / "config" / "auth.local.json"


def runtime_file(pkg: Path) -> Path:
    return Path(pkg) / "config" / "runtime.json"


def write_runtime_json(
    pkg: Path,
    *,
    tenant: str,
    subsystem: str,
    base_url: str = "",
) -> None:
    dest = runtime_file(pkg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "tenant": str(tenant or ""),
                "subsystem": str(subsystem or ""),
                "base_url": str(base_url or "").rstrip("/"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_auth_local(pkg: Path, headers: dict[str, Any] | None) -> None:
    dest = auth_file(pkg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).strip() and value not in (None, "")
    }
    dest.write_text(
        json.dumps({"headers": cleaned}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_runtime(pkg: Path) -> dict[str, str]:
    path = runtime_file(pkg)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "tenant": str(data.get("tenant") or ""),
        "subsystem": str(data.get("subsystem") or ""),
        "base_url": str(data.get("base_url") or ""),
    }


def base_url_from_steps(steps: list[dict[str, Any]] | None) -> str:
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        raw = str(step.get("url") or "").strip()
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def write_auth_to_export_dir(
    out_dir: str,
    *,
    tenant: str,
    subsystem: str,
    headers: dict[str, Any] | None,
) -> list[str]:
    root = Path(out_dir)
    if not str(out_dir or "").strip() or not root.is_dir():
        return []
    updated: list[str] = []
    for pkg in sorted(root.iterdir()):
        if not pkg.is_dir() or pkg.name.startswith("."):
            continue
        runtime = read_runtime(pkg)
        if runtime.get("tenant") != tenant or runtime.get("subsystem") != subsystem:
            continue
        write_auth_local(pkg, headers)
        updated.append(pkg.name)
    return updated
