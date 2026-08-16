from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Keep each test process's disposable files under the repository runtime root."""
    if config.option.basetemp is None:
        runtime_root = Path(__file__).resolve().parents[2] / ".runtime" / "pytest-temp"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = runtime_root / str(os.getpid())
