"""Commit 10 helper: bind helpers from owner modules and collapse the facade."""
from __future__ import annotations

import ast
import json
from pathlib import Path

BACK = Path(__file__).resolve().parent.parent
PAGE = BACK / "dano" / "execution" / "page"
DANO = BACK / "dano"
TESTS = BACK / "tests"
OWNERS_JSON = BACK / "tests" / "fixtures" / "flow_spec_split" / "symbol_owners.json"

SKIP_DEF_NAMES = {
    "_bind_flow_spec_helpers",
    "_PENDING_FLOW_SPEC_HELPERS",
    "_PENDING_OWNERS",
}

REQUIRED_PUBLIC_API = (
    "ALLOWED_CAPABILITY_KINDS",
    "CapabilityRequestRef",
    "FlowCapability",
    "FlowLink",
    "FlowSpec",
    "FlowStep",
    "ParamField",
    "READ_CAPABILITY_KINDS",
    "RecordedGoal",
    "RequestAnalysis",
    "RequestFact",
    "WRITE_CAPABILITY_KINDS",
    "append_flow_version",
    "apply_client_flow_patch",
    "apply_flow_edits",
    "apply_recording_agent_submission",
    "auto_fix_flow_spec",
    "classify_network_request",
    "dry_run_flow_spec",
    "ensure_flow_version",
    "executable_flow_links",
    "flow_spec_fingerprint",
    "flow_spec_release_payload",
    "flow_spec_required_params",
    "flow_spec_to_api_request",
    "flow_spec_to_client",
    "flow_spec_to_summary",
    "prepare_flow_release_candidate",
    "prepare_flow_spec_for_publish",
    "recording_agent_state",
    "recording_agent_validation",
    "recording_capability_plan_complete",
    "to_flow_spec",
    "validate_flow_spec",
)

EXTRA_PUBLIC_API = (
    "CapabilityDependency",
    "CapabilityField",
    "CapabilityRelation",
    "FlowSpecConflictError",
    "IdentityBinding",
    "RequestFacts",
    "RequestUsage",
    "ReviewItem",
    "SelectBinding",
    "SystemValue",
    "build_review_items",
    "capability_to_flow_spec_view",
    "compile_capability_to_api_request",
    "ensure_recorded_goal",
    "flow_spec_capability_contracts",
    "flow_spec_user_params",
    "orchestrate_flow_capabilities",
    "promote_request_to_step",
    "rebuild_flow_dependencies",
    "refresh_review_items",
    "render_business_description",
    "sync_capability_scoped_views",
    "sync_flow_spec_models",
)

PUBLIC_API = tuple(dict.fromkeys((*REQUIRED_PUBLIC_API, *EXTRA_PUBLIC_API)))

SIDE_EFFECT_MODULES = [
    "dano.execution.page.flow_spec_core.models",
    "dano.execution.page.flow_spec_core.fingerprints",
    "dano.execution.page.flow_spec_core.serialization",
    "dano.execution.page.flow_spec_core.versioning",
    "dano.execution.page.flow_spec_core.normalization",
    "dano.execution.page.flow_spec_core.request_contract",
    "dano.execution.page.flow_spec_core.controlled_edits",
    "dano.execution.page.recording_facts",
    "dano.execution.page.recording_analysis_state",
    "dano.execution.page.recording_agent_contract",
    "dano.execution.page.flow_materialization.titles",
    "dano.execution.page.flow_materialization.request_steps",
    "dano.execution.page.flow_materialization.request_usage",
    "dano.execution.page.flow_materialization.links",
    "dano.execution.page.flow_materialization.hydration",
    "dano.execution.page.flow_materialization.response_maps",
    "dano.execution.page.flow_materialization.field_contracts.common",
    "dano.execution.page.flow_materialization.field_contracts.required",
    "dano.execution.page.flow_materialization.field_contracts.caller_ownership",
    "dano.execution.page.flow_materialization.field_contracts.record_identity",
    "dano.execution.page.flow_materialization.field_contracts.option_projection",
    "dano.execution.page.flow_materialization.field_contracts.option_repair",
    "dano.execution.page.flow_materialization.field_contracts.option_sync",
    "dano.execution.page.flow_materialization.field_contracts.computed",
    "dano.execution.page.flow_materialization.field_contracts.create_form",
    "dano.execution.page.flow_materialization.field_contracts.edit_form",
    "dano.execution.page.flow_materialization.field_contracts.query_form",
    "dano.execution.page.flow_materialization.field_contracts.row_command",
    "dano.execution.page.flow_materialization.field_contracts.page_rules",
    "dano.execution.page.flow_materialization.builder",
    "dano.execution.page.flow_materialization.review_items",
    "dano.execution.page.recording_semantic_index",
    "dano.execution.page.capability_kinds",
    "dano.execution.page.capability_identity",
    "dano.execution.page.capability_semantic",
    "dano.execution.page.capability_nodes",
    "dano.execution.page.capability_refs",
    "dano.execution.page.capability_io",
    "dano.execution.page.capability_views",
    "dano.execution.page.capability_validation",
    "dano.execution.page.capability_repair",
    "dano.execution.page.capability_orchestration",
    "dano.execution.page.capability_contracts",
    "dano.execution.page.capability_compiler",
    "dano.execution.page.flow_client_projection",
    "dano.execution.page.flow_release",
    "dano.execution.page.flow_spec_validate",
    "dano.execution.page.recording_live",
]


def module_name_for(path: Path) -> str:
    rel = path.relative_to(BACK / "dano").with_suffix("").as_posix().replace("/", ".")
    return "dano." + rel


def python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.name != "__pycache__"]


def defined_symbols(tree: ast.AST) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            found[node.name] = "class"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = "function"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in SKIP_DEF_NAMES:
                    found.setdefault(target.id, "constant")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id not in SKIP_DEF_NAMES:
                found.setdefault(node.target.id, "constant")
    return found


def build_owner_map() -> dict[str, str]:
    owners: dict[str, tuple[int, str]] = {}
    for path in python_files(PAGE):
        if path.name == "flow_spec.py":
            continue
        module = module_name_for(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, kind in defined_symbols(tree).items():
            score = 2 if kind in {"class", "function"} else 1
            current = owners.get(name)
            if current is None or score > current[0]:
                owners[name] = (score, module)
    return {name: module for name, (_score, module) in owners.items()}


BIND_TEMPLATE = '''
_PENDING_FLOW_SPEC_HELPERS = {pending}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
'''


def replace_pending_block(text: str, pending: dict[str, str]) -> str:
    prefix = "_PENDING_FLOW_SPEC_HELPERS = "
    start = text.index(prefix)
    new_block = BIND_TEMPLATE.format(pending=repr(pending)).lstrip("\n")
    return text[:start].rstrip() + "\n\n" + new_block


def rewrite_binds(owners: dict[str, str]) -> list[str]:
    missing: list[str] = []
    updated = []
    for path in python_files(PAGE):
        text = path.read_text(encoding="utf-8")
        if "_PENDING_FLOW_SPEC_HELPERS = " not in text:
            continue
        tree = ast.parse(text)
        current_names: list[str] = []
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_PENDING_FLOW_SPEC_HELPERS" for t in node.targets)
            ):
                value = ast.literal_eval(node.value)
                current_names = list(value) if not isinstance(value, dict) else list(value)
                break
        pending: dict[str, str] = {}
        for name in current_names:
            owner = owners.get(name)
            if owner is None:
                missing.append(f"{path.name}:{name}")
                continue
            if owner == module_name_for(path):
                continue
            pending[name] = owner
        path.write_text(replace_pending_block(text, pending), encoding="utf-8")
        updated.append(path.as_posix())
    if missing:
        raise SystemExit("unmapped pending helpers:\n" + "\n".join(missing))
    return updated


def grouped_imports(names: list[str], owners: dict[str, str], public_module: str | None) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for name in names:
        if public_module and not name.startswith("_"):
            grouped.setdefault(public_module, []).append(name)
            continue
        owner = owners.get(name)
        if owner is None:
            raise SystemExit(f"no owner for imported symbol {name}")
        grouped.setdefault(owner, []).append(name)
    return sorted(grouped.items())


def rewrite_import_from(node: ast.ImportFrom, owners: dict[str, str], *, internal: bool) -> list[str] | None:
    if node.module != "dano.execution.page.flow_spec":
        return None
    names = [alias.name for alias in node.names]
    asnames = {alias.name: alias.asname for alias in node.names}
    lines: list[str] = []
    public_module = None if internal else "dano.execution.page.flow_spec"
    # Internal modules should not import the facade, including public names.
    if internal:
        public_module = None
    grouped: dict[str, list[str]] = {}
    for name in names:
        if name == "*":
            raise SystemExit("wildcard import from flow_spec")
        if public_module and not name.startswith("_"):
            owner = public_module
        else:
            owner = owners.get(name)
            if owner is None:
                raise SystemExit(f"no owner for {name}")
        grouped.setdefault(owner, []).append(name)
    for module, group in grouped.items():
        rendered = []
        for name in group:
            alias = asnames.get(name)
            rendered.append(f"{name} as {alias}" if alias else name)
        if len(rendered) == 1:
            lines.append(f"from {module} import {rendered[0]}")
        else:
            inner = ",\n    ".join(rendered)
            lines.append(f"from {module} import (\n    {inner},\n)")
    return lines


def rewrite_source_imports(path: Path, owners: dict[str, str], *, internal: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    replacements: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dano.execution.page.flow_spec":
            lines = rewrite_import_from(node, owners, internal=internal)
            if lines is None:
                continue
            indent = " " * (node.col_offset or 0)
            indented = []
            for line in lines:
                if not line:
                    indented.append(line)
                elif line.startswith("from ") or line.startswith("import "):
                    indented.append(indent + line)
                else:
                    indented.append(indent + line)
            start = node.lineno - 1
            end = (node.end_lineno or node.lineno)
            replacements.append((start, end, "\n".join(indented) + "\n"))
    if not replacements:
        return False
    source_lines = text.splitlines(keepends=True)
    for start, end, new in sorted(replacements, reverse=True):
        source_lines[start:end] = [new]
    path.write_text("".join(source_lines), encoding="utf-8")
    return True


def write_facade(owners: dict[str, str]) -> None:
    public_groups: dict[str, list[str]] = {}
    for name in PUBLIC_API:
        owner = owners.get(name)
        if owner is None:
            raise SystemExit(f"public API missing owner: {name}")
        public_groups.setdefault(owner, []).append(name)

    lines = [
        '"""Compatibility facade for FlowSpec public imports.',
        "",
        "This module re-exports stable public names from their owner modules.",
        "It contains no stage business logic.",
        '"""',
        "from __future__ import annotations",
        "",
        "# Side-effect imports so delayed helper bind can resolve owner modules.",
    ]
    seen_aliases: dict[str, int] = {}
    for module in SIDE_EFFECT_MODULES:
        base = "_" + module.rsplit(".", 1)[-1]
        count = seen_aliases.get(base, 0)
        seen_aliases[base] = count + 1
        alias = base if count == 0 else f"{base}_{count}"
        lines.append(f"import {module} as {alias}")
    lines.append("")
    for module, names in sorted(public_groups.items()):
        inner = ",\n    ".join(names)
        lines.append(f"from {module} import (\n    {inner},\n)")
        lines.append("")
    lines.append("from dano.execution.page.flow_spec_core.models import register_sync_flow_spec_models")
    lines.append("")
    all_names = ",\n    ".join(f'"{name}"' for name in PUBLIC_API)
    lines.append(f"__all__ = (\n    {all_names},\n)")
    lines.append("")
    lines.append("register_sync_flow_spec_models(sync_flow_spec_models)")
    lines.append("")
    lines.append("import sys as _sys")
    lines.append("for _name, _extracted in list(_sys.modules.items()):")
    lines.append("    if (")
    lines.append('        isinstance(_name, str)')
    lines.append('        and _name.startswith("dano.execution.page.")')
    lines.append('        and hasattr(_extracted, "_bind_flow_spec_helpers")')
    lines.append("    ):")
    lines.append("        _extracted._bind_flow_spec_helpers()")
    lines.append("")
    (PAGE / "flow_spec.py").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_materialization_init() -> None:
    (PAGE / "flow_materialization" / "__init__.py").write_text(
        '''"""Stage 5 FlowSpec materialization package."""
from dano.execution.page.flow_materialization.builder import (
    to_flow_spec,
    sync_flow_spec_models,
)

__all__ = ["to_flow_spec", "sync_flow_spec_models"]
''',
        encoding="utf-8",
    )


def slim_core_init() -> None:
    (PAGE / "flow_spec_core" / "__init__.py").write_text(
        '''"""Shared FlowSpec core: models, fingerprints, serialization, versioning."""
from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    RecordedGoal,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    RequestUsage,
    ReviewItem,
    SelectBinding,
    SystemValue,
    register_sync_flow_spec_models,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    flow_spec_fingerprint,
)
from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload
from dano.execution.page.flow_spec_core.versioning import (
    append_flow_version,
    ensure_flow_version,
)

__all__ = [
    "CapabilityDependency",
    "CapabilityField",
    "CapabilityRelation",
    "CapabilityRequestRef",
    "FlowCapability",
    "FlowLink",
    "FlowSpec",
    "FlowSpecConflictError",
    "FlowStep",
    "IdentityBinding",
    "ParamField",
    "RecordedGoal",
    "RequestAnalysis",
    "RequestFact",
    "RequestFacts",
    "RequestUsage",
    "ReviewItem",
    "SelectBinding",
    "SystemValue",
    "append_flow_version",
    "ensure_flow_version",
    "flow_spec_fingerprint",
    "flow_spec_release_payload",
    "register_sync_flow_spec_models",
]
''',
        encoding="utf-8",
    )


def rewrite_callers(owners: dict[str, str]) -> list[str]:
    changed = []
    for path in python_files(PAGE):
        if path.name == "flow_spec.py":
            continue
        internal = True
        # recording_live / replay / compiler are internal owners, not the facade.
        if rewrite_source_imports(path, owners, internal=internal):
            changed.append(str(path))
    for path in python_files(DANO / "onboarding"):
        # Keep public facade imports; rewrite only private _xxx by setting
        # internal=False so public names stay on flow_spec.
        if rewrite_source_imports(path, owners, internal=False):
            changed.append(str(path))
    for path in python_files(DANO / "gateway"):
        if rewrite_source_imports(path, owners, internal=False):
            changed.append(str(path))
    for path in python_files(DANO / "agent_tools"):
        if rewrite_source_imports(path, owners, internal=False):
            changed.append(str(path))
    for path in python_files(DANO / "export"):
        if rewrite_source_imports(path, owners, internal=False):
            changed.append(str(path))
    for path in python_files(TESTS):
        if rewrite_source_imports(path, owners, internal=False):
            changed.append(str(path))
    return changed


def dump_owner_sidecar(owners: dict[str, str]) -> None:
    payload = {
        "generated_by": "scripts/_close_flow_spec_facade.py",
        "public_api": list(PUBLIC_API),
        "owners": owners,
    }
    out = BACK / "tests" / "fixtures" / "flow_spec_split" / "symbol_owner_modules.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    import sys
    owners = build_owner_map()
    dump_owner_sidecar(owners)
    only_callers = "--callers-only" in sys.argv
    only_binds = "--binds-only" in sys.argv
    if only_binds:
        updated = rewrite_binds(owners)
        print("rewrote binds", len(updated))
        return
    if not only_callers:
        updated = rewrite_binds(owners)
        write_facade(owners)
        write_materialization_init()
        slim_core_init()
        print("rewrote binds", len(updated))
    callers = rewrite_callers(owners)
    print("rewrote callers", len(callers))
    print("facade lines", len((PAGE / "flow_spec.py").read_text(encoding="utf-8").splitlines()))
    missing_public = [name for name in PUBLIC_API if name not in owners]
    print("missing public", missing_public)


if __name__ == "__main__":
    main()
