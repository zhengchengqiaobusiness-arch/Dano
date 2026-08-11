from __future__ import annotations

from pathlib import Path

from dano.export.skill_package.validator import validate_skill_documents, validate_skill_package


_SKILL = """---
name: demo-skill
description: Demonstration package
---

## Transport
HTTP JSON

## Preconditions
Set DANO_AUTH_HEADERS.

## Steps
1. Run the capability.
   Done when: the result has `ok: true`.

## Branch exit
Stop when the API reports a terminal state.

## Pitfalls
Do not reuse expired credentials.
"""

_REFERENCE = """# Reference

## API chain
- GET /items -> POST /items verification_id: 550e8400-e29b-41d4-a716-446655440000

## Business hard rules
Stop on failure.

## Fallback browser steps
1. Use visible role/name labels.
"""

_SCRIPT = """import argparse
import json

parser = argparse.ArgumentParser()
parser.parse_args()
print(json.dumps({"ok": True}))
"""


def _valid_package(root: Path) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    (root / "reference.md").write_text(_REFERENCE, encoding="utf-8")
    (scripts / "client.py").write_text(_SCRIPT, encoding="utf-8")
    (scripts / "submit.py").write_text(_SCRIPT, encoding="utf-8")
    (scripts / "verify_submit.py").write_text(_SCRIPT, encoding="utf-8")
    return root


def test_validate_skill_package_accepts_complete_package(tmp_path):
    result = validate_skill_package(_valid_package(tmp_path / "complete"))
    assert result == {"ok": True, "issues": []}


def test_validate_skill_package_rejects_missing_sections_and_verify_script(tmp_path):
    package = _valid_package(tmp_path / "broken")
    (package / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    (package / "scripts" / "verify_submit.py").unlink()
    result = validate_skill_package(package)
    codes = {issue["code"] for issue in result["issues"]}
    assert result["ok"] is False
    assert "skill_section" in codes
    assert "missing_verify" in codes


def test_validate_skill_package_rejects_plaintext_credentials(tmp_path):
    package = _valid_package(tmp_path / "leak")
    with (package / "reference.md").open("a", encoding="utf-8") as stream:
        stream.write("\nAuthorization: Bearer recorded-secret-token-value\n")
    result = validate_skill_package(package)
    assert any(issue["code"] == "credential_leak" for issue in result["issues"])


def test_model_docs_reject_invented_evidence_and_missing_capability_chain():
    result = validate_skill_documents(
        _SKILL,
        _REFERENCE,
        allowed_verification_ids={"550e8400-e29b-41d4-a716-446655440099"},
        required_chain_names={"create_item"},
    )
    codes = {issue["code"] for issue in result["issues"]}
    assert result["ok"] is False
    assert codes == {"missing_api_chain", "ungrounded_verification"}


def test_model_docs_must_label_unverified_write_chain():
    reference = _REFERENCE.replace("GET /items", "create_item: GET /items")
    result = validate_skill_documents(
        _SKILL,
        reference,
        allowed_verification_ids={"550e8400-e29b-41d4-a716-446655440000"},
        required_chain_names={"create_item"},
        required_unverified_chains={"create_item"},
    )
    assert result["ok"] is False
    assert {issue["code"] for issue in result["issues"]} == {"missing_unverified_marker"}


def test_incomplete_reference_package_can_be_inspected_as_warnings(tmp_path):
    reference = tmp_path / "incomplete-reference"
    (reference / "scripts").mkdir(parents=True)
    result = validate_skill_package(reference, missing_as_warnings=True)
    assert result["ok"] is True
    assert result["issues"]
    assert all(issue["severity"] == "warning" for issue in result["issues"])
