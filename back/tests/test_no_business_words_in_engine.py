"""Company-specific vocabulary must stay in tenant packs, never the generic engine."""

from __future__ import annotations

from pathlib import Path
import re


ENGINE_ROOT = Path(__file__).parents[1] / "dano"
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml", ".md"}
PROHIBITED = re.compile("|".join(("请假", "报销", "工单", "A-OA", "A-报销", "seetacloud", "若依", "ruoyi")), re.I)


def test_no_company_vocabulary_in_generic_engine() -> None:
    hits: list[str] = []
    for path in ENGINE_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if "business_packs" in path.parts or "tests" in path.parts:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PROHIBITED.search(line):
                hits.append(f"{path.relative_to(ENGINE_ROOT)}:{line_number}: {line.strip()}")
    assert hits == []
