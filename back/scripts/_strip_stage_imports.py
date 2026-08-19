"""Strip stage imports from core/builder files and bind those names instead."""
from __future__ import annotations

import ast
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "dano" / "execution" / "page"

CORE_BANNED_PREFIXES = (
    "dano.execution.page.recording_facts",
    "dano.execution.page.recording_analysis_state",
    "dano.execution.page.recording_agent_contract",
    "dano.execution.page.recording_live",
    "dano.execution.page.flow_materialization",
    "dano.execution.page.capability_contracts",
    "dano.execution.page.capability_",
    "dano.execution.page.flow_release",
    "dano.execution.page.flow_client_projection",
    "dano.execution.page.flow_spec",
    "dano.onboarding.recording_stage_seven",
)

BUILDER_BANNED_PREFIXES = (
    "dano.execution.page.flow_release",
    "dano.execution.page.capability_contracts",
    "dano.execution.page.capability_",
)


def strip_imports(path: Path, banned: tuple[str, ...], extra_pending: list[str]) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    skip: set[int] = set()
    collected: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if any(node.module == item or node.module.startswith(item) for item in banned):
            for alias in node.names:
                collected.append(alias.asname or alias.name)
            for lineno in range(node.lineno, node.end_lineno + 1):
                skip.add(lineno)
    out = [line for index, line in enumerate(lines, 1) if index not in skip]
    text = "".join(out)
    prefix = "_PENDING_FLOW_SPEC_HELPERS = "
    if prefix not in text:
        listed = ", ".join(repr(item) for item in collected + extra_pending)
        text += f"\n\n{prefix}({listed},)\n\n\n"
        text += '''def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
'''
    else:
        start = text.index(prefix)
        end = text.index(")", start) + 1
        current = ast.literal_eval(text[start:end].split("=", 1)[1].strip())
        merged = tuple(dict.fromkeys(list(current) + collected + extra_pending))
        listed = ", ".join(repr(item) for item in merged)
        text = text[:start] + f"{prefix}({listed},)" + text[end:]
    path.write_text(text, encoding="utf-8")
    print(path.name, "stripped", collected)


def main() -> None:
    strip_imports(PAGE / "flow_spec_core" / "controlled_edits.py", CORE_BANNED_PREFIXES, [])
    strip_imports(
        PAGE / "flow_materialization" / "builder.py",
        BUILDER_BANNED_PREFIXES,
        ["sync_capability_scoped_views"],
    )


if __name__ == "__main__":
    main()
