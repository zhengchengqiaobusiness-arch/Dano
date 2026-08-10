"""Print precision/recall for the hand-labeled recording link traces."""
from __future__ import annotations

import json
from pathlib import Path
import sys

BACK_DIR = Path(__file__).resolve().parents[2]
if str(BACK_DIR) not in sys.path:
    sys.path.insert(0, str(BACK_DIR))

from dano.execution.page.value_tracing import discover_value_links  # noqa: E402


CASES = Path(__file__).with_name("cases.json")


def _signature(item) -> tuple[str, str, str, str]:  # noqa: ANN001
    if isinstance(item, dict):
        return tuple(str(item[key]) for key in (
            "source_request_id", "source_path", "target_request_id", "target_path",
        ))
    return tuple(str(value) for value in item)


def evaluate() -> dict:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    true_positives = false_positives = false_negatives = 0
    results = []
    for case in cases:
        predicted = {_signature(item) for item in discover_value_links(case["requests"])}
        truth = {_signature(item) for item in case["truth"]}
        tp = len(predicted & truth)
        fp = len(predicted - truth)
        fn = len(truth - predicted)
        true_positives += tp
        false_positives += fp
        false_negatives += fn
        results.append({"name": case["name"], "tp": tp, "fp": fp, "fn": fn})
    precision = true_positives / (true_positives + false_positives) if true_positives + false_positives else 1.0
    recall = true_positives / (true_positives + false_negatives) if true_positives + false_negatives else 1.0
    return {
        "case_count": len(cases),
        "truth_count": true_positives + false_negatives,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "cases": results,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
