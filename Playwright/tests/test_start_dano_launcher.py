from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "start-dano.bat"
ATTRIBUTES = ROOT / ".gitattributes"


def _launcher_text() -> str:
    return LAUNCHER.read_text(encoding="utf-8").replace("\r\n", "\n").lower()


def test_launcher_is_portable_and_configurable() -> None:
    script = _launcher_text()

    assert 'set "root=%~dp0"' in script
    assert "setlocal enableextensions disabledelayedexpansion" in script
    assert "enableextensions enabledelayedexpansion" not in script
    assert "e:\\python\\condaenv\\dano-backend\\python.exe" not in script
    assert "dano_python" in script
    assert "dano_backend_port" in script
    assert "dano_frontend_port" in script
    assert "py -3.12" in script
    assert "conda run -n dano-backend python" in script


def test_launcher_preflights_every_runtime_dependency() -> None:
    script = _launcher_text()

    required_contracts = (
        "where node",
        "where npm",
        "back\\pyproject.toml",
        "playwright\\pyproject.toml",
        "tomllib",
        "subprocess.check_call",
        "playwright install chromium",
        "playwright\\package-lock.json",
        "skillfrontend\\package-lock.json",
        "back\\agent\\package-lock.json",
        "npm ci --no-audit --no-fund",
        "22.19.0",
        "asyncpg.connect",
    )
    for contract in required_contracts:
        assert contract in script

    assert '-e "%backend_dir%[page]"' not in script
    assert '-e "%playwright_dir%[browser]"' not in script


def test_launcher_waits_for_both_services_before_opening_browser() -> None:
    script = _launcher_text()

    assert ':wait_for_backend' in script
    assert ':wait_for_frontend' in script
    assert '/health' in script
    assert 'invoke-webrequest' in script
    assert '--strictport' in script
    assert 'set "frontend_page=%frontend_url%/recording"' in script
    assert 'start "" "%frontend_page%"' in script
    assert script.index('call :wait_for_backend') < script.index('start "" "%frontend_page%"')
    assert script.index('call :wait_for_frontend') < script.index('start "" "%frontend_page%"')


def test_launcher_waits_without_reading_console_input() -> None:
    script = _launcher_text()

    assert "timeout /t 1" not in script
    assert script.count("ping.exe -n 2 127.0.0.1") == 2


def test_launcher_has_side_effect_free_check_mode() -> None:
    script = _launcher_text()

    assert 'if /i "%~1"=="check"' in script
    assert "dano_launcher_check_ok" in script
    assert script.index("dano_launcher_check_ok") < script.index(':start_services')


def test_launcher_avoids_cmd_redirection_in_python_version_check() -> None:
    script = _launcher_text()

    assert "^>=" not in script
    assert "sys.version_info[1] in range(12, 100)" in script


def test_launcher_prepends_current_recording_source() -> None:
    script = _launcher_text()

    assert 'set "pythonpath=%playwright_src%;%backend_dir%;%pythonpath%"' in script
    assert 'set "pythonpath=%playwright_src%;%backend_dir%"' in script


def test_batch_files_are_forced_to_windows_line_endings() -> None:
    raw = LAUNCHER.read_bytes()
    attributes = ATTRIBUTES.read_text(encoding="utf-8") if ATTRIBUTES.exists() else ""

    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert "*.bat text eol=crlf" in attributes


def test_launcher_does_not_reuse_unknown_branch_processes_by_default() -> None:
    script = _launcher_text()

    assert "dano_reuse_services" in script
    assert "refusing to reuse it by default" in script
    assert "set dano_reuse_services=1" in script


def test_launcher_rejects_the_example_pi_key_placeholder() -> None:
    script = _launcher_text()

    assert "key.startswith(chr(60))" in script


def test_launcher_keeps_pi_stub_mode_explicit() -> None:
    script = _launcher_text()
    public_args = script[: script.index("echo.\necho [dano] project:")]
    check = script[
        script.index(":check_pi_configuration") : script.index(":chromium_check")
    ]

    clear = public_args.index('set "pi_stub="')
    explicit = public_args.index('if /i "%~1"=="--stub" set "pi_stub=1"')
    child = public_args.index('if /i "%~1"=="__dano_backend" goto :child_backend')
    assert child < clear < explicit
    assert 'set "pi_stub=1"' not in check
    assert "error: pi api key is missing" in check


def test_launcher_restores_its_title_after_npm_preflight() -> None:
    script = _launcher_text()

    for project in ("playwright_dir", "frontend_dir", "agent_dir"):
        call = f'call :ensure_node_dependencies "%{project}%"'
        start = script.index(call)
        following_lines = script[start:].splitlines()[:4]
        assert "title dano launcher" in following_lines


def test_launcher_validates_port_range_and_distinct_ports() -> None:
    script = _launcher_text()

    assert "call :validate_port" in script
    assert "65535" in script
    assert "backend and frontend ports must be different" in script


def test_launcher_forces_frontend_to_the_selected_backend() -> None:
    script = _launcher_text()

    assert 'set "dano_gateway=%backend_url%"' in script
    assert "if not defined dano_gateway" not in script


def test_launcher_checks_declared_versions_and_yaml() -> None:
    script = _launcher_text()

    assert "importlib.metadata" in script
    assert "packaging.requirements" in script
    assert "import yaml" in script


def test_launcher_waits_for_recording_v3_readiness() -> None:
    script = _launcher_text()

    assert "/recording-v3/health" in script
    assert "recording v3 is ready" in script


def test_launcher_cleans_up_only_processes_started_by_this_run() -> None:
    script = _launcher_text()

    for contract in (
        "backend_started",
        "frontend_started",
        "backend_pid",
        "frontend_pid",
        "backend_start_ticks",
        "frontend_start_ticks",
        "__dano_backend",
        "__dano_frontend",
        ":cleanup_started_services",
        ":kill_owned_tree",
        "taskkill /pid",
        "call :cleanup_started_services",
    ):
        assert contract in script


def test_launcher_spawns_child_modes_without_losing_path_quotes() -> None:
    script = _launcher_text()
    start = script.index("\n:spawn_log_window\n")
    spawn = script[start : script.index("\n:child_backend\n", start)]

    assert "$launcherdir=[io.path]::getdirectoryname($env:dano_self)" in spawn
    assert "$command='call start-dano.bat {0}'" in spawn
    assert "-workingdirectory $launcherdir" in spawn
    assert "'call \"\"{0}\"\" {1}'" not in spawn


def test_launcher_prefers_the_dedicated_conda_environment_over_path_python() -> None:
    script = _launcher_text()

    assert script.index("rem conda fallback") < script.index("for /f \"delims=\" %%p in ('where python")
    assert "where conda.exe" in script
    assert "--no-capture-output" in script


def test_launcher_checks_installed_node_tree_against_the_lockfile() -> None:
    script = _launcher_text()

    assert ":node_lock_check" in script
    assert "node_modules\\.package-lock.json" in script
    assert "root.packages" in script
    assert "installed.packages" in script


@pytest.mark.skipif(os.name != "nt", reason="Windows batch integration test")
@pytest.mark.parametrize(
    ("backend_port", "frontend_port", "message"),
    (
        ("65536", "15173", "must be between 1 and 65535"),
        ("18077", "18077", "backend and frontend ports must be different"),
    ),
)
def test_launcher_rejects_invalid_port_configurations(
    backend_port: str,
    frontend_port: str,
    message: str,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DANO_BACKEND_PORT": backend_port,
            "DANO_FRONTEND_PORT": frontend_port,
            "DANO_NO_PAUSE": "1",
        }
    )

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(LAUNCHER), "check", "--stub"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode != 0
    assert message in output
