from __future__ import annotations

from pathlib import Path

MIN_FLOW_PY = """\
from __future__ import annotations
import argparse
import json

def main():
    parser = argparse.ArgumentParser(description="Run a recorded business route")
    parser.add_argument("--route", default="query_then_create", help="query_then_create")
    parser.add_argument("--input-json", default="{}")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"ok": True, "route": args.route, "routes": ["query_then_create"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
"""


def write_skill4_draft(root: Path, **files: str | None) -> Path:
    defaults: dict[str, str] = {
        "SKILL.md": "---\nname: demo\ndescription: 查询汇报统计并新增工作日报\n---\n\n# demo\nkept-handbook\n",
        "scripts/search.py": "print('ok')\n",
        "scripts/flow.py": MIN_FLOW_PY,
    }
    defaults.update({key: value for key, value in files.items() if value is not None})
    for rel, content in defaults.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root
