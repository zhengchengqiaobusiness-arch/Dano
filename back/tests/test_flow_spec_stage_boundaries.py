"""Architecture invariants for the FlowSpec eight-stage split.

Checks that can already be enforced stay hard. Checks that depend on modules
created in later split commits become active as soon as those modules exist.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

BACK = Path(__file__).resolve().parent.parent
PAGE = BACK / "dano" / "execution" / "page"
ONBOARDING = BACK / "dano" / "onboarding"
PIPELINE = BACK / "tests" / "fixtures" / "flow_spec_split" / "mechanical_pipeline.json"
OWNERS = BACK / "tests" / "fixtures" / "flow_spec_split" / "symbol_owners.json"

STAGE_MODULES = {
    "recording_facts": "stage2",
    "recording_analysis_state": "stage3",
    "recording_agent_contract": "stage4",
    "flow_materialization": "stage5",
    "capability_contracts": "stage6",
    "capability_compiler": "stage6",
    "flow_release": "stage8",
    "flow_client_projection": "stage8",
}


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _module_imports(path: Path) -> list[str]:
    tree = _parse(path)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    return found


def _call_names_in_function(source: Path, func_name: str) -> list[str]:
    tree = _parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            names: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    if isinstance(func, ast.Name):
                        names.append(func.id)
            return names
    raise AssertionError(f"{func_name} not found in {source}")


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.name != "__pycache__"]


def test_mechanical_field_contract_order_is_frozen() -> None:
    expected = json.loads(PIPELINE.read_text(encoding="utf-8"))
    flow_spec = PAGE / "flow_spec.py"
    if flow_spec.exists() and "def _apply_mechanical_field_contracts" in flow_spec.read_text(encoding="utf-8"):
        source = flow_spec
    else:
        source = PAGE / "flow_materialization" / "builder.py"
        assert source.exists(), "field-contract pipeline owner missing"
    actual = _call_names_in_function(source, "_apply_mechanical_field_contracts")
    assert actual == expected["apply_mechanical_field_contracts"]


def test_computed_runtime_prefix_order_is_frozen() -> None:
    expected = json.loads(PIPELINE.read_text(encoding="utf-8"))["infer_computed_runtime_fields_prefix"]
    candidates = [
        PAGE / "flow_spec.py",
        PAGE / "flow_materialization" / "field_contracts" / "computed.py",
    ]
    source = next(
        path for path in candidates
        if path.exists() and "def _infer_computed_runtime_fields" in path.read_text(encoding="utf-8")
    )
    actual = _call_names_in_function(source, "_infer_computed_runtime_fields")[:2]
    assert actual == expected


def test_flow_spec_core_does_not_import_stage_modules() -> None:
    core = PAGE / "flow_spec_core"
    if not core.exists():
        return
    banned = (
        "dano.execution.page.recording_facts",
        "dano.execution.page.recording_analysis_state",
        "dano.execution.page.recording_agent_contract",
        "dano.execution.page.flow_materialization",
        "dano.execution.page.capability_contracts",
        "dano.execution.page.capability_compiler",
        "dano.execution.page.flow_release",
        "dano.execution.page.flow_client_projection",
        "dano.execution.page.flow_spec",
        "dano.onboarding.recording_stage_seven",
    )
    offenders: list[str] = []
    for path in _python_files(core):
        for module in _module_imports(path):
            if any(module == item or module.startswith(item + ".") for item in banned):
                offenders.append(f"{path.name}:{module}")
    assert offenders == [], offenders


def test_materialization_does_not_import_stage_seven_or_eight() -> None:
    root = PAGE / "flow_materialization"
    if not root.exists():
        return
    banned = (
        "dano.onboarding.recording_stage_seven",
        "dano.execution.page.flow_release",
    )
    offenders: list[str] = []
    for path in _python_files(root):
        for module in _module_imports(path):
            if any(module == item or module.startswith(item + ".") for item in banned):
                offenders.append(f"{path}:{module}")
    assert offenders == [], offenders


def test_capability_compiler_does_not_import_stage_seven() -> None:
    path = PAGE / "capability_compiler.py"
    imports = _module_imports(path)
    assert "dano.onboarding.recording_stage_seven" not in imports


def test_stage_seven_does_not_import_field_contracts() -> None:
    path = ONBOARDING / "recording_stage_seven.py"
    imports = _module_imports(path)
    offenders = [
        item for item in imports
        if "flow_materialization.field_contracts" in item
        or item.endswith("recording_agent_contract")
        or item.endswith("flow_release")
    ]
    assert offenders == [], offenders


def test_flow_release_does_not_import_agent_contract() -> None:
    path = PAGE / "flow_release.py"
    if not path.exists():
        return
    imports = _module_imports(path)
    assert "dano.execution.page.recording_agent_contract" not in imports


def test_new_internal_modules_do_not_import_flow_spec_facade() -> None:
    if not (PAGE / "flow_spec_core").exists():
        return
    offenders: list[str] = []
    skip = {PAGE / "flow_spec.py"}
    for path in _python_files(PAGE):
        if path in skip:
            continue
        rel = path.relative_to(PAGE).as_posix()
        if rel.split("/")[0] not in {
            "flow_spec_core",
            "flow_materialization",
        } and path.name not in {
            "recording_facts.py",
            "recording_analysis_state.py",
            "recording_agent_contract.py",
            "capability_contracts.py",
            "capability_compiler.py",
            "capability_kinds.py",
            "capability_identity.py",
            "capability_semantic.py",
            "capability_nodes.py",
            "capability_refs.py",
            "capability_io.py",
            "capability_views.py",
            "capability_validation.py",
            "capability_repair.py",
            "capability_orchestration.py",
            "flow_release.py",
            "flow_client_projection.py",
            "flow_spec_validate.py",
        }:
            continue
        tree = _parse(path)
        for node in tree.body:
            imported = []
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            for module in imported:
                if module == "dano.execution.page.flow_spec":
                    offenders.append(rel)
    assert offenders == [], offenders


def test_no_duplicate_top_level_function_definitions_across_new_modules() -> None:
    """Once modules exist, a public name may be defined in only one owner module."""
    owners = json.loads(OWNERS.read_text(encoding="utf-8"))
    if not (PAGE / "flow_spec_core").exists():
        # Pre-split: flow_spec.py currently defines _projection_path_score twice.
        # Recorded as split-out finding; do not "fix" it in this task.
        tree = _parse(PAGE / "flow_spec.py")
        func_counts: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                func_counts[node.name] = func_counts.get(node.name, 0) + 1
        duplicates = {name: count for name, count in func_counts.items() if count > 1}
        assert duplicates == {"_projection_path_score": 2}, duplicates
        return

    defined: dict[str, list[str]] = {}
    split_roots = {
        PAGE / "flow_spec_core",
        PAGE / "flow_materialization",
    }
    split_files = {
        PAGE / "recording_facts.py",
        PAGE / "recording_analysis_state.py",
        PAGE / "recording_agent_contract.py",
        PAGE / "capability_contracts.py",
        PAGE / "capability_kinds.py",
        PAGE / "capability_identity.py",
        PAGE / "capability_semantic.py",
        PAGE / "capability_nodes.py",
        PAGE / "capability_refs.py",
        PAGE / "capability_io.py",
        PAGE / "capability_views.py",
        PAGE / "capability_validation.py",
        PAGE / "capability_repair.py",
        PAGE / "capability_orchestration.py",
        PAGE / "flow_release.py",
        PAGE / "flow_client_projection.py",
        PAGE / "flow_spec_validate.py",
    }
    for path in _python_files(PAGE):
        if path.name == "flow_spec.py":
            continue
        if not (path in split_files or any(root in path.parents for root in split_roots)):
            continue
        for node in _parse(path).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                defined.setdefault(node.name, []).append(path.as_posix())
    duplicates = {
        name: paths
        for name, paths in defined.items()
        if len(set(paths)) > 1 and not name.startswith("test_") and name not in {"_bind_flow_spec_helpers"}
    }
    # Re-exports via assignment are not function definitions; this only flags
    # two `def`/`class` bodies for the same name.
    assert duplicates == {}, duplicates
    assert owners["counts"]["function"] == 573


def test_stage_seven_authority_stays_in_onboarding() -> None:
    assert (ONBOARDING / "recording_stage_seven.py").exists()
    assert not (PAGE / "flow_materialization" / "recording_stage_seven.py").exists()
    text = (PAGE / "flow_spec.py").read_text(encoding="utf-8")
    assert "class StageSevenStatus" not in text
    assert "class StageSevenVerdict" not in text


def test_flow_spec_facade_is_explicit_reexport_only() -> None:
    source = PAGE / "flow_spec.py"
    text = source.read_text(encoding="utf-8")
    tree = _parse(source)
    assert len(text.splitlines()) <= 400
    assert "__all__" in {node.targets[0].id for node in tree.body if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name)}
    business = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name not in {"_bind_flow_spec_helpers"}
    ]
    assert business == [], business
    assert "def __getattr__" not in text
    assert "import *" not in text
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            body_lines = (node.end_lineno or node.lineno) - node.lineno + 1
            assert body_lines <= 30, node.name


def test_production_code_does_not_import_private_flow_spec_symbols() -> None:
    offenders: list[str] = []
    for path in _python_files(BACK / "dano"):
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "dano.execution.page.flow_spec"
            ):
                privates = [alias.name for alias in node.names if alias.name.startswith("_")]
                if privates:
                    offenders.append(f"{path.relative_to(BACK)}:{privates}")
    assert offenders == [], offenders


def test_flow_spec_facade_import_has_no_cycle() -> None:
    import importlib
    importlib.invalidate_caches()
    module = importlib.import_module("dano.execution.page.flow_spec")
    assert hasattr(module, "FlowSpec")
    assert hasattr(module, "to_flow_spec")
    assert module.to_flow_spec.__module__ != "dano.execution.page.flow_spec"
    assert module.FlowSpec.__module__ == "dano.execution.page.flow_spec_core.models"


def test_versioning_owner_executes_without_facade_import_side_effects() -> None:
    code = """
from dano.execution.page.flow_spec_core.models import FlowSpec
from dano.execution.page.flow_spec_core.versioning import append_flow_version
spec = append_flow_version(FlowSpec(), "owner-runtime-check")
assert spec.meta["current_version"] == 1
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACK,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
