"""Regression guard for retired recording/export implementations.

The live V2 flow has one recording entrypoint and uses the maintained export
implementations in ``agent_skills`` and ``skill_package``.  Previously, an
unwired second exporter lived beside them and made it easy to fix code that
production could never execute.
"""

from __future__ import annotations

import ast
from pathlib import Path


BACK_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = BACK_ROOT / "dano"

RETIRED_MODULES = {
    "dano.export.live_skill",
    "dano.export.recording_skill",
    "dano.export.skill_forge_gate",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_retired_recording_export_modules_do_not_exist_or_reenter_runtime() -> None:
    retired_paths = {
        SOURCE_ROOT.joinpath(*module.split(".")[1:]).with_suffix(".py")
        for module in RETIRED_MODULES
    }
    existing = sorted(str(path.relative_to(BACK_ROOT)) for path in retired_paths if path.exists())
    assert not existing, f"retired parallel exporters still exist: {existing}"

    offenders: dict[str, list[str]] = {}
    for path in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        hits = sorted(_imports(path) & RETIRED_MODULES)
        if hits:
            offenders[str(path.relative_to(BACK_ROOT))] = hits
    assert not offenders, f"runtime imports retired recording exporters: {offenders}"
