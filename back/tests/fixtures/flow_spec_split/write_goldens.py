from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "tests"
MODULE = TESTS / "test_flow_spec_serialization_compat.py"


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(TESTS))
    spec = importlib.util.spec_from_file_location("flow_spec_serialization_compat", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.write_goldens()
    print("goldens written to", module.GOLDEN_DIR)


if __name__ == "__main__":
    main()
