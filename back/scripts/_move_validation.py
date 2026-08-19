from pathlib import Path

src = Path(__file__).resolve().parents[1] / "dano" / "execution" / "page" / "flow_spec_core" / "validation.py"
dst = Path(__file__).resolve().parents[1] / "dano" / "execution" / "page" / "flow_spec_validate.py"
text = src.read_text(encoding="utf-8")
if not text.startswith('"""'):
    text = '"""Composed FlowSpec validation across materialized, capability, and release inputs."""\n' + text
pending = """

_PENDING_FLOW_SPEC_HELPERS = (
    '_sanitize_capability_nodes',
    '_prune_empty_capabilities',
    '_normalize_capability_references',
    '_active_capability_step_ids',
    '_capability_validation_report',
    '_capability_node_step_ids',
    '_capability_param_enum_issue',
    '_capability_param_enum_warning',
)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
"""
if "_PENDING_FLOW_SPEC_HELPERS" not in text:
    text = text.rstrip() + pending + "\n"
dst.write_text(text, encoding="utf-8")
src.unlink()
print("moved", dst)
