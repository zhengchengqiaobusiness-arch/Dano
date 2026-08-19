"""Dump AST inventory of flow_spec.py for the split task."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dano" / "execution" / "page" / "flow_spec.py"


def main() -> None:
    source = SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    classes, funcs, assigns, imports = [], [], [], []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(
                {
                    "name": node.name,
                    "lineno": node.lineno,
                    "end": node.end_lineno,
                    "lines": node.end_lineno - node.lineno + 1,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(
                {
                    "name": node.name,
                    "lineno": node.lineno,
                    "end": node.end_lineno,
                    "lines": node.end_lineno - node.lineno + 1,
                }
            )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns.append(
                        {"name": target.id, "lineno": node.lineno, "end": node.end_lineno}
                    )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigns.append(
                {
                    "name": node.target.id,
                    "lineno": node.lineno,
                    "end": node.end_lineno,
                }
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.append(alias.asname or alias.name)

    out = {
        "path": str(SRC.as_posix()),
        "bytes": SRC.stat().st_size,
        "lines": source.count("\n") + (0 if source.endswith("\n") else 1),
        "class_count": len(classes),
        "function_count": len(funcs),
        "assign_count": len(assigns),
        "imported_names": imports,
        "classes": classes,
        "functions": funcs,
        "assigns": assigns,
    }
    dest = ROOT / "tests" / "fixtures" / "flow_spec_split" / "ast_inventory.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest} classes={len(classes)} funcs={len(funcs)} assigns={len(assigns)}")
    print("---CLASSES---")
    for item in classes:
        print(f"{item['lineno']:5d}-{item['end']:5d} {item['name']}")
    print("---ASSIGNS---")
    for item in assigns:
        print(f"{item['lineno']:5d} {item['name']}")
    print("---FUNCS---")
    for item in funcs:
        print(f"{item['lineno']:5d}-{item['end']:5d} ({item['lines']:4d}) {item['name']}")


if __name__ == "__main__":
    main()
