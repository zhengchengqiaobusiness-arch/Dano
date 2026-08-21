"""Deleting a Skill from the catalog must remove exported package folders."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from dano.export.agent_skills import _slug
from dano.export.skill_package.renderer import package_slug
from dano.gateway.app import (
    _cleanup_export_folders,
    _export_slugs_for_manifest,
    _known_export_dirs,
    _normalize_export_dir,
)


def test_export_slugs_include_package_and_proxy_folders() -> None:
    slugs = _export_slugs_for_manifest({"name": "oa.leave_create", "subsystem": "oa"})
    assert _slug("oa.leave_create") in slugs
    assert package_slug("oa.leave_create") in slugs
    assert package_slug("oa.leave_create").endswith("-package")


def test_cleanup_removes_package_folder_left_by_default_export(tmp_path: Path) -> None:
    skill_id = "oa.leave_create"
    package = tmp_path / package_slug(skill_id)
    proxy = tmp_path / _slug(skill_id)
    sibling = tmp_path / package_slug("oa.leave_query")
    leftover = tmp_path / f".{package_slug(skill_id)}-abcd1234"
    backup = tmp_path / f".{package_slug(skill_id)}.old-deadbeef"
    for folder in (package, proxy, sibling, leftover, backup):
        folder.mkdir()
        (folder / "SKILL.md").write_text("keep-or-drop", encoding="utf-8")

    removed = _cleanup_export_folders(str(tmp_path), _export_slugs_for_manifest({"name": skill_id}))

    assert not package.exists()
    assert not proxy.exists()
    assert not leftover.exists()
    assert not backup.exists()
    assert sibling.exists()
    assert {Path(item).name for item in removed} == {
        package.name,
        proxy.name,
        leftover.name,
        backup.name,
    }


def test_cleanup_does_not_remove_longer_sibling_stage_folder(tmp_path: Path) -> None:
    owned = tmp_path / "dano-oa-leave"
    other_stage = tmp_path / ".dano-oa-leave-create-abcd1234"
    owned.mkdir()
    other_stage.mkdir()

    _cleanup_export_folders(str(tmp_path), {"dano-oa-leave"})

    assert not owned.exists()
    assert other_stage.exists()


def test_known_export_dirs_include_agent_skills_parent() -> None:
    raw = r"E:\skills\agent-skills"
    assert _normalize_export_dir(raw) == r"E:\skills"
    with patch("dano.execution.page.sessions.get_export_dirs", return_value=[raw]):
        dirs = _known_export_dirs()
    assert raw in dirs
    assert r"E:\skills" in dirs
