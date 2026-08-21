"""Write frozen request/spec/plan snapshots for the sales-order gap."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from dano.export.skill_package.renderer import _fallback_skill_md  # noqa: E402
from dano.onboarding.skill_generation.planner import propose_deterministic_plan  # noqa: E402
from stage8_sale_order_fixture import (  # noqa: E402
    FIXTURE_DIR,
    sale_order_request,
    sale_order_spec,
    sale_order_verified_ids,
)
from types import SimpleNamespace  # noqa: E402


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    spec = sale_order_spec()
    request = sale_order_request()
    verified = sale_order_verified_ids(spec)
    plan = propose_deterministic_plan(spec, request, verified, "fp-sale-order")
    (FIXTURE_DIR / "request.json").write_text(
        json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (FIXTURE_DIR / "spec.json").write_text(
        json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (FIXTURE_DIR / "current_plan.json").write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    skill = SimpleNamespace(
        skill_id="admin.dianshixinxi.com:90.erp.372468ecf111",
        title=request.title,
        action="sale-order",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={
            "_skill_plan": plan.model_dump(mode="json"),
            "capabilities": [
                {
                    "capability_id": cap.capability_id,
                    "name": cap.name,
                    "title": cap.title,
                    "kind": cap.kind,
                    "input_schema": cap.input_schema,
                }
                for cap in spec.capabilities
            ],
        },
    )
    plans = [
        {
            "name": cap.name,
            "capability_id": cap.capability_id,
            "title": cap.title,
            "script": cap.name,
            "requires_confirmation": cap.kind != "query",
            "requires_verify": cap.kind != "query",
            "input_schema": cap.input_schema,
        }
        for cap in spec.capabilities
    ]
    text = _fallback_skill_md(skill, "dano-admin-dianshixinxi-com-90-erp-372468ecf111-package", plans, spec)
    (FIXTURE_DIR / "current_skill_excerpt.md").write_text(text, encoding="utf-8")
    print(f"wrote {FIXTURE_DIR}")
    print(f"routes={len(plan.routes)} combo={sum(1 for route in plan.routes if len(route.capability_sequence) > 1)}")


if __name__ == "__main__":
    main()
