"""Move named top-level symbols from flow_spec.py into a new module."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "dano" / "execution" / "page"
SRC = PAGE / "flow_spec.py"
OWNERS = ROOT / "tests" / "fixtures" / "flow_spec_split" / "symbol_owners.json"

STDLIB = {
    "Any": "from typing import Any",
    "re": "import re",
    "json": "import json",
    "copy": "import copy",
    "hashlib": "import hashlib",
    "uuid": "import uuid",
    "datetime": "from datetime import datetime, timezone",
    "timezone": "from datetime import datetime, timezone",
    "unquote": "from urllib.parse import unquote, urlparse, parse_qs, urlencode",
    "urlparse": "from urllib.parse import unquote, urlparse, parse_qs, urlencode",
    "parse_qs": "from urllib.parse import unquote, urlparse, parse_qs, urlencode",
    "urlencode": "from urllib.parse import unquote, urlparse, parse_qs, urlencode",
}

MODELS = {
    "ParamField", "SelectBinding", "IdentityBinding", "SystemValue", "FlowStep",
    "FlowLink", "RequestFact", "RequestAnalysis", "RequestUsage", "RequestFacts",
    "CapabilityRequestRef", "CapabilityField", "CapabilityDependency",
    "CapabilityRelation", "ReviewItem", "FlowCapability", "RecordedGoal",
    "FlowSpec", "FlowSpecConflictError",
}

CAPTURE = {
    "_is_const_value", "_fact_path_tokens", "_leaf_paths", "_parse_body",
    "_is_system_timestamp", "bounded_response_sample", "normalized_leaf_paths",
    "as_list_payload", "apply_page_enum_options", "build_api_request",
    "classify_request_role", "discover_step_links", "select_dependency_source",
    "page_enum_selects", "extract_auth_headers", "flatten_body", "infer_success_rule",
    "write_requests", "looks_internal_param_name", "looks_like_auth_write",
    "looks_like_read_request", "parse_recorded_request_body", "self_check",
    "substitute", "suggest_assignee_names", "suggest_fact_check", "suggest_identity",
    "suggest_list_selects", "suggest_select_names", "suggest_selects", "_is_idlike",
    "_multipart_contains_file", "_pick_label_key",
}

KNOWN_MODULES = {
    "LIVE_RECORDING_AGENT_OPS": "dano.execution.page.recording_live",
    "apply_recording_agent_edit": "dano.execution.page.recording_live",
    "compact_model_payload": "dano.execution.page.recording_live",
    "recording_agent_evidence_issues": "dano.execution.page.recording_live",
    "merge_live_agent_state": "dano.execution.page.recording_live",
    "FieldRef": "dano.execution.page.recording_field_identity",
    "canonical_wire_path": "dano.execution.page.recording_field_identity",
    "resolve_field_ref": "dano.execution.page.recording_field_identity",
    "discover_response_key_maps": "dano.execution.page.value_tracing",
    "discover_workflow_value_links": "dano.execution.page.value_tracing",
}


def source_segments(lines: list[str], tree: ast.AST, names: set[str]) -> list[tuple[int, int, str, str]]:
    found: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for node in tree.body:
        node_names: list[str] = []
        kind = "other"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node_names = [node.name]
            kind = "def"
        elif isinstance(node, ast.Assign):
            kind = "assign"
            for target in node.targets:
                if isinstance(target, ast.Name):
                    node_names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            kind = "assign"
            node_names.append(node.target.id)
        if any(name in names for name in node_names):
            start, end = node.lineno, node.end_lineno
            found.append((start, end, "".join(lines[start - 1:end]), kind))
            seen.update(node_names)
    missing = sorted(names - seen)
    if missing:
        raise SystemExit(f"symbols not found in flow_spec.py: {missing}")
    found.sort()
    return found


def top_level_names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                found.add(alias.asname or alias.name)
    return found


def free_names(segments: list[tuple[int, int, str, str]]) -> set[str]:
    loaded: set[str] = set()
    defined: set[str] = set()
    for _s, _e, text, _kind in segments:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            if isinstance(node, ast.arg):
                defined.add(node.arg)
    return loaded - defined - set(dir(__import__("builtins")))


def write_module(dest: Path, header: str, segments: list[tuple[int, int, str, str]], extras: list[str], docstring: str = "") -> None:
    chunks = [text.rstrip() for _s, _e, text, _k in segments]
    prefix = header.rstrip()
    if docstring:
        prefix = f'"""{docstring}"""\n{prefix}'
    dest.write_text(prefix + "\n\n\n" + "\n\n\n".join(chunks) + "\n", encoding="utf-8")
    if extras:
        print("lazy helpers expected from flow_spec:", ", ".join(extras))


def strip_and_import(lines: list[str], segments: list[tuple[int, int, str, str]], module: str, names: list[str]) -> str:
    skip: set[int] = set()
    for start, end, _t, _k in segments:
        skip.update(range(start, end + 1))
    out = [line for index, line in enumerate(lines, 1) if index not in skip]
    bind_alias = "_" + module.replace(".", "_")
    bind_block = (
        f"import dano.execution.page.{module} as {bind_alias}\n"
        f"if hasattr({bind_alias}, '_bind_flow_spec_helpers'):\n"
        f"    {bind_alias}._bind_flow_spec_helpers()\n"
    )
    import_block = (
        f"\nfrom dano.execution.page.{module} import (\n"
        + "".join(f"    {name},\n" for name in names)
        + ")\n"
        + bind_block
    )
    text = "".join(out)
    marker = "register_sync_flow_spec_models(sync_flow_spec_models)\n"
    if marker in text:
        text = text.replace(marker, import_block + "\n" + marker, 1)
    else:
        text += import_block
    return text


def owner_names(owner: str) -> list[str]:
    payload = json.loads(OWNERS.read_text(encoding="utf-8"))
    names = []
    for row in payload["symbols"].values():
        if row["owner"] == owner:
            names.append(row["name"])
    return unique_names(names)


def module_names(module: str) -> list[str]:
    payload = json.loads(OWNERS.read_text(encoding="utf-8"))
    names = []
    for row in payload["symbols"].values():
        if row["module"] == module:
            names.append(row["name"])
    return unique_names(names)


def unique_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def build_header(free: set[str], moved: set[str], extra_imports: list[str], available: set[str]) -> tuple[str, list[str]]:
    lines = [
        'from __future__ import annotations',
        "",
    ]
    std_added: set[str] = set()
    for name in sorted(free):
        if name in STDLIB:
            stmt = STDLIB[name]
            if stmt not in std_added:
                lines.append(stmt)
                std_added.add(stmt)
    model_names = sorted(free & MODELS)
    if model_names:
        lines.append("from dano.execution.page.flow_spec_core.models import (")
        lines.extend(f"    {name}," for name in model_names)
        lines.append(")")
    capture_names = sorted(free & CAPTURE)
    if capture_names:
        lines.append("from dano.execution.page.request_capture import (")
        lines.extend(f"    {name}," for name in capture_names)
        lines.append(")")
    lines.extend(extra_imports)
    leftover = sorted(
        name for name in free
        if name not in STDLIB
        and name not in MODELS
        and name not in CAPTURE
        and name not in moved
        and name in available
        and name not in {"Field", "BaseModel", "ConfigDict", "model_validator", "ValidationError"}
    )
    return "\n".join(lines) + "\n", leftover


def defined_locally(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
    return found


def peer_symbol_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    allowed_files = {
        "recording_facts.py",
        "recording_analysis_state.py",
        "recording_agent_contract.py",
        "capability_contracts.py",
        "flow_release.py",
        "flow_client_projection.py",
    }
    for path in PAGE.rglob("*.py"):
        if path.name == "flow_spec.py" or path.name.startswith("_"):
            continue
        rel = path.relative_to(PAGE)
        if rel.parts[0] not in {"flow_spec_core", "flow_materialization"} and path.name not in allowed_files:
            continue
        module = "dano.execution.page." + rel.with_suffix("").as_posix().replace("/", ".")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in defined_locally(tree):
            if name in {"_bind_flow_spec_helpers", "_PENDING_FLOW_SPEC_HELPERS"}:
                continue
            mapping.setdefault(name, module)
    return mapping


def inject_legacy_imports(module_text: str, leftover: list[str]) -> str:
    if not leftover:
        return module_text
    listed = ", ".join(repr(item) for item in leftover)
    helper = f'''

_PENDING_FLOW_SPEC_HELPERS = ({listed},)


def _bind_flow_spec_helpers() -> None:
    import dano.execution.page.flow_spec as _flow_spec
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
'''
    return module_text + helper


def main() -> None:
    dest_mod = sys.argv[1]
    raw_names = sys.argv[2:]
    use_closure = "--closure" in raw_names
    src_mod = "flow_spec"
    filtered: list[str] = []
    for item in raw_names:
        if item == "--closure":
            use_closure = True
        elif item.startswith("--src="):
            src_mod = item.split("=", 1)[1]
        else:
            filtered.append(item)
    raw_names = filtered
    names: list[str] = []
    for item in raw_names:
        if item.startswith("owner:"):
            names.extend(owner_names(item.split(":", 1)[1]))
        elif item.startswith("module:"):
            names.extend(module_names(item.split(":", 1)[1]))
        else:
            names.append(item)
    dest = PAGE / f"{dest_mod.replace('.', '/')}.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_path = PAGE / f"{src_mod.replace('.', '/')}.py"
    source = src_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    local = defined_locally(tree)
    available = top_level_names(tree)
    peers = peer_symbol_map()
    wanted = set(names) & local
    missing = sorted(set(names) - local)
    if missing:
        print("skip already-moved or absent:", ", ".join(missing))
    if not wanted:
        print("nothing to move")
        return
    if use_closure:
        while True:
            segments = source_segments(lines, tree, wanted)
            free = free_names(segments)
            extra = sorted((free - wanted) & local)
            if not extra:
                break
            wanted.update(extra)
            print("closure added", extra)
    else:
        segments = source_segments(lines, tree, wanted)
        free = free_names(segments)
    extra_imports: list[str] = []
    leftover_peers: dict[str, list[str]] = {}
    unresolved = []
    for name in sorted((free - wanted) - set(STDLIB) - MODELS - CAPTURE):
        if name in peers and peers[name] != f"dano.execution.page.{src_mod}":
            leftover_peers.setdefault(peers[name], []).append(name)
        elif name in KNOWN_MODULES:
            leftover_peers.setdefault(KNOWN_MODULES[name], []).append(name)
        elif name in local:
            unresolved.append(name)
        elif src_mod != "flow_spec" and name.startswith("_"):
            unresolved.append(name)
    for module, symbols in leftover_peers.items():
        extra_imports.append(f"from {module} import (")
        extra_imports.extend(f"    {symbol}," for symbol in symbols)
        extra_imports.append(")")
    header, leftover = build_header(free, wanted, extra_imports, available)
    leftover = unresolved
    write_module(dest, header, segments, leftover)
    dest.write_text(inject_legacy_imports(dest.read_text(encoding="utf-8"), leftover), encoding="utf-8")
    src_path.write_text(strip_and_import(lines, segments, dest_mod, sorted(wanted)), encoding="utf-8")
    print(f"moved {len(wanted)} symbols to {dest} from {src_mod}")


if __name__ == "__main__":
    main()
