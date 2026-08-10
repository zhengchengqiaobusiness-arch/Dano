"""Machine validator for self-contained skill packages."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import yaml


_REQUIRED_SKILL_SECTIONS = ("Transport", "Preconditions", "Steps", "Branch exit", "Pitfalls")
_VERIFICATION_ID_RE = re.compile(
    r"\bverification_id\s*[:=]?\s*[`\[]?[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.I,
)
_API_LINE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD)\b|(?:->|→)", re.I)
_PLAINTEXT_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:Bearer|Basic|Token)\s+(?![<{($])[A-Za-z0-9._~+/-]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)[\"'](?:password|secret|access[_-]?token|refresh[_-]?token|session|cookie)[\"']\s*:\s*[\"'](?![<{($*])[^\"']{8,}[\"']"),
)


def _issue(code: str, message: str, path: Path, *, warning: bool = False) -> dict:
    return {
        "severity": "warning" if warning else "error",
        "code": code,
        "path": str(path),
        "message": message,
    }


def _read(path: Path, issues: list[dict], *, missing_as_warnings: bool) -> str:
    if not path.is_file():
        issues.append(_issue("missing_file", f"missing required file: {path.name}", path, warning=missing_as_warnings))
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append(_issue("encoding", "file must be UTF-8 text", path))
        return ""


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    match = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", text, re.S)
    if not match:
        return {}
    value = yaml.safe_load(match.group(1))
    return value if isinstance(value, dict) else {}


def _section(text: str, title: str) -> str:
    match = re.search(
        rf"(?ims)^##+\s+{re.escape(title)}\s*$\n(.*?)(?=^##+\s+|\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def _check_skill(path: Path, text: str, issues: list[dict]) -> None:
    metadata = _frontmatter(text)
    for key in ("name", "description"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            issues.append(_issue("frontmatter", f"SKILL.md frontmatter requires non-empty {key}", path))
    for title in _REQUIRED_SKILL_SECTIONS:
        if not _section(text, title):
            issues.append(_issue("skill_section", f"SKILL.md requires section: {title}", path))
    steps = _section(text, "Steps")
    step_markers = re.findall(r"(?m)^(?:###\s+|\s*\d+[.)]\s+)", steps)
    done_when = re.findall(r"(?im)\bDone\s+when\s*:", steps)
    if steps and (not done_when or len(done_when) < max(1, len(step_markers))):
        issues.append(_issue("done_when", "every documented step must include `Done when:`", path))


def _check_reference(path: Path, text: str, issues: list[dict]) -> None:
    chain = _section(text, "API chain")
    if not chain:
        issues.append(_issue("api_chain", "reference.md requires a non-empty API chain section", path))
        return
    lines = [line.strip() for line in chain.splitlines() if _API_LINE_RE.search(line)]
    if not lines:
        issues.append(_issue("api_chain", "API chain must describe at least one request chain", path))
    for line in lines:
        if not _VERIFICATION_ID_RE.search(line) and "unverified" not in line.casefold():
            issues.append(_issue("chain_evidence", f"API chain lacks verification_id or unverified marker: {line}", path))


def _check_scripts(scripts: Path, issues: list[dict], *, missing_as_warnings: bool) -> None:
    if not scripts.is_dir():
        issues.append(_issue("missing_scripts", "missing required scripts directory", scripts, warning=missing_as_warnings))
        return
    client = scripts / "client.py"
    if not client.is_file():
        issues.append(_issue("missing_client", "scripts/client.py is required", client, warning=missing_as_warnings))
    python_scripts = sorted(scripts.glob("*.py"))
    capabilities = [path for path in python_scripts if path.name != "client.py" and not path.name.startswith("verify_")]
    if not capabilities:
        issues.append(_issue("missing_capability", "at least one capability script is required", scripts, warning=missing_as_warnings))
    for capability in capabilities:
        verify = scripts / f"verify_{capability.name}"
        if not verify.is_file():
            issues.append(_issue("missing_verify", f"missing verifier for {capability.name}", verify))
    for script in python_scripts:
        source = _read(script, issues, missing_as_warnings=False)
        if source and not re.search(r"\bjson\.(?:dump|dumps)\s*\(", source):
            issues.append(_issue("json_stdout", "script must emit operational JSON", script))
        try:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=str(scripts),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(_issue("script_help", f"--help failed: {exc}", script))
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-500:]
            issues.append(_issue("script_help", f"--help exited {completed.returncode}: {detail}", script))


def _check_credentials(pkg_dir: Path, issues: list[dict]) -> None:
    for path in pkg_dir.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in _PLAINTEXT_CREDENTIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append(_issue("credential_leak", f"possible plaintext credential: {match.group(0)[:24]}", path))
                break


def validate_skill_package(pkg_dir: Path, *, missing_as_warnings: bool = False) -> dict:
    """Validate one package and return ``{ok, issues}`` with stable issue codes."""
    root = Path(pkg_dir)
    issues: list[dict] = []
    if not root.is_dir():
        return {"ok": False, "issues": [_issue("missing_package", "package directory does not exist", root)]}
    skill_path = root / "SKILL.md"
    reference_path = root / "reference.md"
    skill = _read(skill_path, issues, missing_as_warnings=missing_as_warnings)
    reference = _read(reference_path, issues, missing_as_warnings=missing_as_warnings)
    if skill:
        _check_skill(skill_path, skill, issues)
    if reference:
        _check_reference(reference_path, reference, issues)
    _check_scripts(root / "scripts", issues, missing_as_warnings=missing_as_warnings)
    _check_credentials(root, issues)
    return {"ok": not any(issue["severity"] == "error" for issue in issues), "issues": issues}
