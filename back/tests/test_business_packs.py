"""Tenant business metadata is optional, data-only, and isolated from the engine."""

from __future__ import annotations

import json

from dano.business_packs import (
    action_meta_for,
    business_subsystems,
    default_subsystem,
    dialects_for,
    load_business_pack,
)
from dano.business_packs.loader import clear_business_pack_cache
from dano.capabilities.oa_templates import match_template
from dano.catalog.manifest import to_manifest
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem
from dano.shared.std_fields import COMMON_FIELDS, standard_fields_for


def test_missing_pack_is_empty_and_keeps_only_common_fields() -> None:
    assert load_business_pack("missing-tenant") == {}
    assert business_subsystems("missing-tenant") == []
    assert default_subsystem("missing-tenant") == ""
    assert dialects_for("missing-tenant") == []
    assert standard_fields_for("missing-tenant") == COMMON_FIELDS


def test_company_pack_supplies_current_suite_without_engine_defaults() -> None:
    pack = load_business_pack("a-company")
    assert pack["tenant"] == "a-company"
    assert len(business_subsystems("a-company")) == 3
    assert default_subsystem("a-company") == business_subsystems("a-company")[-1]
    assert action_meta_for("a-company")["create_leave"]["fact_check_query"] == "query_balance"
    assert len(standard_fields_for("a-company")) > len(COMMON_FIELDS)

    spec = {
        "paths": {"/workflow/handle/startFlow": {"post": {}}},
        "components": {"schemas": {"AjaxResult": {}}},
    }
    assert match_template(spec) is None
    assert match_template(spec, tenant="a-company").name == "ruoyi-flowable"

    packed_skill = SkillSpec(
        skill_id="workflow.create_leave",
        tenant="a-company",
        subsystem=Subsystem("workflow"),
        action="create_leave",
        risk_level=RiskLevel.L3,
    )
    assert to_manifest(packed_skill).title == pack["action_titles"]["create_leave"]
    assert to_manifest(packed_skill.model_copy(update={"tenant": ""})).title == "create_leave"


def test_json_pack_directory_can_be_reloaded(monkeypatch, tmp_path) -> None:
    custom = {
        "tenant": "tenant-two",
        "subsystems": ["inventory"],
        "default_subsystem": "inventory",
        "action_titles": {"list_stock": "List stock"},
    }
    (tmp_path / "tenant-two.json").write_text(json.dumps(custom), encoding="utf-8")
    monkeypatch.setenv("DANO_BUSINESS_PACK_DIR", str(tmp_path))
    clear_business_pack_cache()
    try:
        assert load_business_pack("tenant-two") == custom
        assert load_business_pack("../tenant-two") == {}
    finally:
        clear_business_pack_cache()
