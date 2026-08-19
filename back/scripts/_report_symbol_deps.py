"""Report free names used by a set of flow_spec.py symbols."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dano" / "execution" / "page" / "flow_spec.py"
OWNERS = ROOT / "tests" / "fixtures" / "flow_spec_split" / "symbol_owners.json"


class NameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.defined: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.defined.add(node.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined.add(node.name)
        for arg in node.args.args + node.args.kwonlyargs:
            self.defined.add(arg.arg)
        if node.args.vararg:
            self.defined.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.defined.add(node.args.kwarg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined.add(node.name)
        self.generic_visit(node)


def top_level_symbols(tree: ast.AST) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = node
    return found


def main() -> None:
    owner = sys.argv[1] if len(sys.argv) > 1 else "stage2"
    owners = json.loads(OWNERS.read_text(encoding="utf-8"))
    wanted = {row["name"] for row in owners["symbols"].values() if row["owner"] == owner}
    source = SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = top_level_symbols(tree)
    collector = NameCollector()
    missing_defs = sorted(name for name in wanted if name not in defined)
    for name in sorted(wanted):
        if name in defined:
            collector.visit(defined[name])
    builtins = set(dir(__import__("builtins")))
    free = collector.loaded - collector.defined - builtins - wanted
    local = sorted(name for name in free if name in defined)
    unknown = sorted(name for name in free if name not in defined)
    print("wanted", len(wanted), "present", len(wanted) - len(missing_defs), "missing", missing_defs)
    print("local helpers still in flow_spec", len(local))
    for name in local:
        row = next((r for r in owners["symbols"].values() if r["name"] == name), None)
        print(f"  {name:50s} {(row or {}).get('owner')} {(row or {}).get('module')}")
    print("unknown/imported", len(unknown))
    for name in unknown:
        print(f"  {name}")


if __name__ == "__main__":
    main()
