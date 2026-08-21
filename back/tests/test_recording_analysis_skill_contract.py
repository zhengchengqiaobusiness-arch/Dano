"""Stage 4 reasoning contract for the recording-analysis Skill."""

from pathlib import Path


SKILL = (
    Path(__file__).parents[1]
    / "agent"
    / "recording-pi"
    / "skills"
    / "analyze-recording-evidence"
    / "SKILL.md"
)


def test_skill_requires_action_request_reconciliation_before_final_plan() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "action-to-request ledger" in text
    assert "standalone inspect/detail" in text


def test_skill_uses_general_unknown_field_evidence_order() -> None:
    text = SKILL.read_text(encoding="utf-8")
    expected = (
        "exact structural identity",
        "semantic content",
        "same recorded value",
        "relative form or table position",
    )
    positions = [text.index(phrase) for phrase in expected]
    assert positions == sorted(positions)


def test_skill_reconciles_every_editable_control_and_required_marker() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "editable-control inventory" in text
    assert "required-control inventory" in text
