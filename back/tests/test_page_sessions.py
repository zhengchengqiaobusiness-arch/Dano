from __future__ import annotations

import json

from dano.execution.page import sessions


def test_save_session_keeps_main_playwright_compatible_and_loads_sidecar(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(sessions, "_DIR", tmp_path)
    extended = {
        "cookies": [{"name": "sid", "value": "cookie", "domain": "example.test", "path": "/"}],
        "origins": [{"origin": "https://example.test", "localStorage": [
            {"name": "access", "value": "local"},
        ]}],
        sessions.SESSION_STORAGE_STATE_KEY: {
            "https://example.test": [{"name": "access", "value": "session"}],
        },
    }

    path = sessions.save_session("tenant", "system/name", extended)

    assert path == str(sessions.session_file("tenant", "system/name"))
    main = json.loads(sessions.session_file("tenant", "system/name").read_text(encoding="utf-8"))
    assert sessions.SESSION_STORAGE_STATE_KEY not in main
    assert main == {"cookies": extended["cookies"], "origins": extended["origins"]}
    assert sessions.session_path_if_exists("tenant", "system/name") == path
    assert sessions.load_session_state("tenant", "system/name") == extended


def test_load_session_state_is_legacy_compatible_and_new_snapshot_removes_stale_sidecar(
    monkeypatch, tmp_path,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(sessions, "_DIR", tmp_path)
    legacy = {"cookies": [], "origins": []}
    sessions.session_file("tenant", "system").write_text(json.dumps(legacy), encoding="utf-8")

    assert sessions.load_session_state("tenant", "system") == legacy

    sessions.session_storage_file("tenant", "system").write_text(
        json.dumps({"https://example.test": [{"name": "old", "value": "credential"}]}),
        encoding="utf-8",
    )
    sessions.save_session("tenant", "system", legacy)

    assert not sessions.session_storage_file("tenant", "system").exists()
    assert sessions.load_session_state("tenant", "system") == legacy
