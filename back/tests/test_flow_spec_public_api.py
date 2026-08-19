"""Lock FlowSpec public imports that external modules currently depend on."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import dano.execution.page.flow_spec as flow_spec

BACK = Path(__file__).resolve().parent
OWNERS = BACK / "fixtures" / "flow_spec_split" / "symbol_owners.json"
FLOW_SPEC = BACK.parent / "dano" / "execution" / "page" / "flow_spec.py"

# Document-required compatibility names, plus every public name currently imported
# from dano.execution.page.flow_spec by production or tests.
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


def _top_level_definitions(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            found[node.name] = "class"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = "function"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = "constant"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = "constant"
    return found


def test_required_public_api_is_importable() -> None:
    missing = [name for name in REQUIRED_PUBLIC_API if not hasattr(flow_spec, name)]
    assert missing == [], missing


def test_symbol_owner_map_covers_current_flow_spec_definitions() -> None:
    owners = json.loads(OWNERS.read_text(encoding="utf-8"))
    allowed = {"shared", "stage2", "stage3", "stage4", "stage5", "stage6", "stage7", "stage8", "compatibility-only"}
    mapped_names = {row["name"] for row in owners["symbols"].values()}
    defined = _top_level_definitions(FLOW_SPEC)
    unmapped = sorted(
        name
        for name in defined
        if name not in mapped_names and name not in {"__all__", "_OWNER_MODULES"}
    )
    assert unmapped == [], unmapped
    bad_owners = sorted(
        {
            f"{row['name']}={row['owner']}"
            for row in owners["symbols"].values()
            if row["owner"] not in allowed
        }
    )
    assert bad_owners == [], bad_owners


def test_owner_map_has_no_unknown_placeholder() -> None:
    owners = json.loads(OWNERS.read_text(encoding="utf-8"))
    unknown = [
        row["name"]
        for row in owners["symbols"].values()
        if "unknown" in row["owner"] or "暂时" in (row.get("note") or "")
    ]
    assert unknown == []


def test_baseline_inventory_counts() -> None:
    owners = json.loads(OWNERS.read_text(encoding="utf-8"))
    assert owners["source_lines"] == 24949
    assert owners["counts"]["class"] == 19
    assert owners["counts"]["function"] == 573
    assert owners["baseline_head"] == "7e6f15097cb1704dbedd87df5ca64de0403103ec"
