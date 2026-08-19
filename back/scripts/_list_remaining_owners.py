"""List remaining flow_spec.py symbols grouped by destination module."""
from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dano" / "execution" / "page" / "flow_spec.py"
OWNERS = ROOT / "tests" / "fixtures" / "flow_spec_split" / "symbol_owners.json"


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


def main() -> None:
    owners = json.loads(OWNERS.read_text(encoding="utf-8"))["symbols"]
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    local = defined_locally(tree)
    by_mod: dict[str, list[str]] = defaultdict(list)
    unknown: list[str] = []
    for name in sorted(local):
        row = None
        for item in owners.values():
            if item["name"] == name:
                row = item
                break
        if row is None:
            unknown.append(name)
            continue
        by_mod[row["module"]].append(name)
    print("remaining", len(local), "unknown", len(unknown))
    for module in sorted(by_mod):
        names = by_mod[module]
        print(f"\n{module} ({len(names)})")
        print(" ".join(names))
    if unknown:
        print("\nUNKNOWN")
        print(" ".join(unknown))


if __name__ == "__main__":
    main()
