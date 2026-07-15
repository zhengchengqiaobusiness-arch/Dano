from __future__ import annotations

import ast
from pathlib import Path
import tomllib

import dano_recording
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.runtime import CaptureRuntime
from dano_recording.evidence.js_ast_worker import JSStaticAnalyzer


ROOT = Path(__file__).parents[1]


def test_runtime_javascript_is_installable_package_data() -> None:
    package_root = Path(dano_recording.__file__).resolve().parent
    runtime = CaptureRuntime(FactLedger(tenant="tenant-a", recording_id="recording-a"))
    assert runtime._browser_scripts
    assert all(path.is_file() and path.is_relative_to(package_root) for path in runtime._browser_scripts)

    worker_path = JSStaticAnalyzer().worker_path.resolve()
    assert worker_path.is_file()
    assert worker_path.is_relative_to(package_root)

    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = set(
        configuration["tool"]["setuptools"]["package-data"]["dano_recording"]
    )
    assert {
        "pi/runtime/*.mjs",
        "_resources/browser/*.js",
        "_resources/js_analysis/*.mjs",
    } <= package_data
    excluded_data = set(
        configuration["tool"]["setuptools"]["exclude-package-data"]["*"]
    )
    assert {"__pycache__/*", "*.py[cod]"} <= excluded_data


def test_v3_does_not_import_legacy_recording_or_python_llm() -> None:
    banned = (
        "dano.execution.page.flow_spec",
        "dano.execution.page.request_capture",
        "dano.execution.page.recorder",
        "dano.onboarding.page_onboard",
        "dano.review",
        "dano.infra.llm",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "dano_recording").rglob("*.py")
    )
    assert not [name for name in banned if name in source]


def test_v3_import_graph_has_no_legacy_or_python_model_cache_backdoor() -> None:
    banned_import_prefixes = (
        "dano.execution.page",
        "dano.onboarding",
        "dano.review",
        "dano.infra.llm",
    )
    banned_runtime_names = {
        "cached_singleflight",
        "canonical_cache_key",
        "llm_response_cache",
        "openai_compat_client",
        "reviewboard",
        "run_pi",
    }
    violations: list[str] = []
    for path in (ROOT / "src" / "dano_recording").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                if name.startswith(banned_import_prefixes):
                    violations.append(f"{path.relative_to(ROOT)} imports {name}")
            if isinstance(node, ast.Name) and node.id.casefold() in banned_runtime_names:
                violations.append(
                    f"{path.relative_to(ROOT)} references {node.id} at {node.lineno}"
                )
            if isinstance(node, ast.Attribute) and node.attr.casefold() in banned_runtime_names:
                violations.append(
                    f"{path.relative_to(ROOT)} references {node.attr} at {node.lineno}"
                )
    assert violations == []


def test_v3_has_one_field_inference_and_one_capability_planner() -> None:
    definitions: dict[str, list[str]] = {
        "infer_field": [],
        "plan_capabilities": [],
        "build_capabilities": [],
    }
    for path in (ROOT / "src" / "dano_recording").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in definitions:
                    definitions[node.name].append(str(path.relative_to(ROOT)))

    assert definitions["infer_field"] == [
        str(Path("src/dano_recording/field_inference.py"))
    ]
    assert definitions["plan_capabilities"] == [
        str(Path("src/dano_recording/capability_planner.py"))
    ]
    assert definitions["build_capabilities"] == []
    assert not (ROOT / "src" / "dano_recording" / "analysis" / "capability_builder.py").exists()


def test_submit_batch_only_exists_in_silent_normalization_boundaries() -> None:
    allowed = {
        Path("src/dano_recording/capability_planner.py"),
        Path("src/dano_recording/compiler/pipeline.py"),
        Path("src/dano_recording/flow_migration.py"),
        Path("src/dano_recording/publish/asset_projection.py"),
    }
    found = {
        path.relative_to(ROOT)
        for path in (ROOT / "src" / "dano_recording").rglob("*.py")
        if "submit_batch" in path.read_text(encoding="utf-8")
    }
    assert found <= allowed


def test_migration_defines_all_recording_tables_and_immutable_facts() -> None:
    migration = (ROOT.parent / "back" / "migrations" / "014_recording_v3.sql").read_text(
        encoding="utf-8"
    )
    for table in (
        "recording_sessions",
        "recording_facts",
        "recording_revisions",
        "recording_operations",
        "recording_pi_sessions",
        "recording_pi_events",
        "recording_artifacts",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    assert "recording_facts_immutable" in migration
    assert "'dom_mutation'" in migration
    assert "UNIQUE (recording_id)" in migration
    assert "operation_id        TEXT        PRIMARY KEY" in migration
