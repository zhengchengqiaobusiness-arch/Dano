from __future__ import annotations

from pathlib import Path

from dano.execution.page.sessions import LINUX_EXPORT_DIR, default_export_dir


def test_linux_default_export_dir_uses_runtime_data(monkeypatch) -> None:
    import dano.execution.page.sessions as sessions

    monkeypatch.setattr(sessions.sys, "platform", "linux")
    assert default_export_dir() == LINUX_EXPORT_DIR
    assert default_export_dir() == "/opt/dano/runtime-data/.agents/skills/"


def test_windows_default_export_dir_stays_repo_export(monkeypatch) -> None:
    import dano.execution.page.sessions as sessions

    monkeypatch.setattr(sessions.sys, "platform", "win32")
    assert Path(default_export_dir()).name == "export"
