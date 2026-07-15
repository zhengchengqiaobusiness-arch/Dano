from __future__ import annotations

from pathlib import Path
from types import ModuleType

from dano import recording_v3


def test_recording_loader_prefers_an_installed_package(monkeypatch, tmp_path: Path) -> None:
    package_dir = tmp_path / "site-packages" / "dano_recording"
    package_dir.mkdir(parents=True)
    package_file = package_dir / "__init__.py"
    package_file.write_text("", encoding="utf-8")

    installed = ModuleType("dano_recording")
    installed.__file__ = str(package_file)
    monkeypatch.setitem(__import__("sys").modules, "dano_recording", installed)
    monkeypatch.setattr(
        recording_v3,
        "__file__",
        str(tmp_path / "site-packages" / "dano" / "recording_v3.py"),
    )

    assert recording_v3.ensure_recording_package() == package_dir
