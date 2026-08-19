"""Add missing owner binds for free names used by extracted modules."""
from __future__ import annotations

import ast

import scripts._close_flow_spec_facade as close

PAGE = close.PAGE
SKIP = close.SKIP_DEF_NAMES | {
    "annotations",
}


def imported_names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found.add(alias.asname or alias.name)
    return found


def local_names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                found.add(arg.arg)
            if node.args.vararg:
                found.add(node.args.vararg.arg)
            if node.args.kwarg:
                found.add(node.args.kwarg.arg)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.add(node.id)
        if isinstance(node, ast.ExceptHandler) and node.name:
            found.add(node.name)
        if isinstance(node, ast.alias):
            found.add(node.asname or node.name.split(".")[-1])
    return found


def pending_dict(tree: ast.AST) -> dict[str, str]:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_PENDING_FLOW_SPEC_HELPERS" for t in node.targets)
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, (list, tuple)):
                return {}
    return {}


def main() -> None:
    owners = close.build_owner_map()
    builtins = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
    missing_report: list[str] = []
    for path in close.python_files(PAGE):
        if path.name == "flow_spec.py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        defined = set(close.defined_symbols(tree)) | imported_names(tree) | SKIP
        pending = pending_dict(tree)
        defined |= set(pending)
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        locals_ = local_names(tree)
        missing = sorted(
            name
            for name in used
            if name not in defined
            and name not in locals_
            and name not in builtins
            and not name.startswith("__")
        )
        extras = {name: owners[name] for name in missing if name in owners}
        unknown = [name for name in missing if name not in owners]
        if unknown:
            missing_report.append(f"{path.relative_to(PAGE)}: {unknown}")
        prefix = "_PENDING_FLOW_SPEC_HELPERS = "
        if not extras and prefix not in text:
            continue
        if not extras:
            continue
        pending.update(extras)
        module = close.module_name_for(path)
        pending = {name: owner for name, owner in pending.items() if owner != module}
        if prefix in text:
            path.write_text(close.replace_pending_block(text, pending), encoding="utf-8")
        else:
            block = close.BIND_TEMPLATE.format(pending=repr(pending)).lstrip("\n")
            path.write_text(text.rstrip() + "\n\n" + block, encoding="utf-8")
        print(f"updated {path.name} +{len(extras)}")
    if missing_report:
        print("UNKNOWN FREE NAMES:")
        print("\n".join(missing_report))


if __name__ == "__main__":
    main()
