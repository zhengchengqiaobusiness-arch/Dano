"""Load optional tenant business metadata from JSON or YAML files."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any

import yaml


_PACKS = Path(__file__).with_name("packs")
_SAFE_TENANT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _pack_dir() -> Path:
    configured = os.environ.get("DANO_BUSINESS_PACK_DIR", "").strip()
    return Path(configured).expanduser() if configured else _PACKS


@lru_cache(maxsize=256)
def _load(tenant: str, directory: str) -> dict[str, Any]:
    name = str(tenant or "").strip()
    if not name or not _SAFE_TENANT.fullmatch(name):
        return {}
    root = Path(directory)
    for suffix in (".yaml", ".yml", ".json"):
        path = root / f"{name}{suffix}"
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if suffix == ".json" else yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError(f"business pack must be an object: {path}")
        declared = str(data.get("tenant") or name)
        if declared != name:
            raise ValueError(f"business pack tenant mismatch: {path}")
        return data
    return {}


def load_business_pack(tenant: str) -> dict[str, Any]:
    """Return a defensive copy; a missing tenant pack is an empty object."""
    return deepcopy(_load(str(tenant or ""), str(_pack_dir().resolve())))


def clear_business_pack_cache() -> None:
    """Refresh file-backed packs after deployment or in tests."""
    _load.cache_clear()


def _objects(tenant: str, key: str) -> list[dict[str, Any]]:
    value = load_business_pack(tenant).get(key) or []
    return [dict(item) for item in value if isinstance(item, dict)]


def business_subsystems(tenant: str) -> list[str]:
    return [str(value) for value in load_business_pack(tenant).get("subsystems") or [] if str(value)]


def default_subsystem(tenant: str) -> str:
    return str(load_business_pack(tenant).get("default_subsystem") or "")


def action_meta_for(tenant: str) -> dict[str, dict[str, Any]]:
    value = load_business_pack(tenant).get("action_meta") or {}
    return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}


def action_titles_for(tenant: str) -> dict[str, str]:
    value = load_business_pack(tenant).get("action_titles") or {}
    return {str(key): str(item) for key, item in value.items() if str(item)}


def standard_fields_for(tenant: str) -> list[dict[str, Any]]:
    return _objects(tenant, "standard_fields")


def dialects_for(tenant: str) -> list[dict[str, Any]]:
    return _objects(tenant, "dialects")


def system_templates_for(tenant: str) -> list[dict[str, Any]]:
    return _objects(tenant, "system_templates")
